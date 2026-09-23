import json
import os
from datetime import date, timedelta
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

from utils import to_decimal


def get_database_url() -> str:
    database_url = os.getenv("DATABASE_URL", "").strip()

    if not database_url:
        raise RuntimeError("Variável DATABASE_URL não configurada.")

    return database_url


def connect(database_url: str | None = None):
    database_url = database_url or get_database_url()

    if "sslmode=" in database_url:
        return psycopg2.connect(database_url)

    return psycopg2.connect(database_url, sslmode="require")


def save_monthly_generation_snapshot(
    station_id: str,
    report_date: date,
    generation_kwh,
) -> None:
    if not station_id:
        raise ValueError("station_id não pode ser vazio.")

    year_month = report_date.strftime("%Y-%m")
    generation = to_decimal(generation_kwh)

    query = """
        INSERT INTO monthly_generation (
            station_id,
            year_month,
            generation_kwh,
            last_report_date,
            updated_at
        )
        VALUES (%s, %s, %s, %s, NOW())
        ON CONFLICT (station_id, year_month)
        DO UPDATE SET
            generation_kwh = GREATEST(
                monthly_generation.generation_kwh,
                EXCLUDED.generation_kwh
            ),
            last_report_date = GREATEST(
                monthly_generation.last_report_date,
                EXCLUDED.last_report_date
            ),
            updated_at = NOW();
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (
                    str(station_id),
                    year_month,
                    generation,
                    report_date.isoformat(),
                ),
            )


def fetch_generation_for_month(
    year_month: str,
    station_id: str | None = None,
) -> tuple[str, Any] | None:
    station_id = (station_id or "").strip()

    if station_id:
        query = """
            SELECT station_id, generation_kwh
            FROM monthly_generation
            WHERE station_id = %s
              AND year_month = %s
            LIMIT 1;
        """
        params = (station_id, year_month)
    else:
        query = """
            SELECT station_id, generation_kwh
            FROM monthly_generation
            WHERE year_month = %s
            ORDER BY updated_at DESC
            LIMIT 1;
        """
        params = (year_month,)

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            row = cursor.fetchone()

    if not row:
        return None

    return str(row[0]), to_decimal(row[1])


def save_daily_generation(
    provider: str,
    station_id: str,
    report_date: date,
    generation_day_kwh,
    generation_month_kwh,
    inverter_status: str = "",
    device_sn: str = "",
) -> None:
    query = """
        INSERT INTO daily_generation (
            provider,
            station_id,
            report_date,
            generation_day_kwh,
            generation_month_kwh,
            inverter_status,
            device_sn,
            collected_at,
            updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
        ON CONFLICT (provider, station_id, report_date)
        DO UPDATE SET
            generation_day_kwh = EXCLUDED.generation_day_kwh,
            generation_month_kwh = GREATEST(
                daily_generation.generation_month_kwh,
                EXCLUDED.generation_month_kwh
            ),
            inverter_status = EXCLUDED.inverter_status,
            device_sn = EXCLUDED.device_sn,
            collected_at = NOW(),
            updated_at = NOW();
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (
                    provider,
                    str(station_id),
                    report_date.isoformat(),
                    to_decimal(generation_day_kwh),
                    to_decimal(generation_month_kwh),
                    inverter_status,
                    device_sn,
                ),
            )


def save_daily_weather(
    provider: str,
    station_id: str,
    report_date: date,
    latitude,
    longitude,
    weather: dict[str, Any],
) -> None:
    query = """
        INSERT INTO daily_weather (
            FORNECEDOR,
            IDUSINA,
            DATARELATORIO,
            LATITUDE,
            LONGITUDE,
            PERCENTUALNUVENS,
            CHUVAMM,
            RADIACAOSOLARWHM2,
            HORASSOL,
            TEMPERATURAMINIMAC,
            TEMPERATURAMAXIMAC,
            CLASSIFICACAOCLIMA,
            PROVEDORCLIMA,
            DADOSBRUTOS,
            COLETADOEM,
            ATUALIZADOEM
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s::jsonb, NOW(), NOW()
        )
        ON CONFLICT (FORNECEDOR, IDUSINA, DATARELATORIO)
        DO UPDATE SET
            LATITUDE = EXCLUDED.LATITUDE,
            LONGITUDE = EXCLUDED.LONGITUDE,
            PERCENTUALNUVENS = EXCLUDED.PERCENTUALNUVENS,
            CHUVAMM = EXCLUDED.CHUVAMM,
            RADIACAOSOLARWHM2 = EXCLUDED.RADIACAOSOLARWHM2,
            HORASSOL = EXCLUDED.HORASSOL,
            TEMPERATURAMINIMAC = EXCLUDED.TEMPERATURAMINIMAC,
            TEMPERATURAMAXIMAC = EXCLUDED.TEMPERATURAMAXIMAC,
            CLASSIFICACAOCLIMA = EXCLUDED.CLASSIFICACAOCLIMA,
            PROVEDORCLIMA = EXCLUDED.PROVEDORCLIMA,
            DADOSBRUTOS = EXCLUDED.DADOSBRUTOS,
            COLETADOEM = NOW(),
            ATUALIZADOEM = NOW();
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (
                    provider,
                    str(station_id),
                    report_date.isoformat(),
                    to_decimal(latitude),
                    to_decimal(longitude),
                    to_decimal(weather.get("PERCENTUALNUVENS")),
                    to_decimal(weather.get("CHUVAMM")),
                    to_decimal(weather.get("RADIACAOSOLARWHM2")),
                    to_decimal(weather.get("HORASSOL")),
                    to_decimal(weather.get("TEMPERATURAMINIMAC")),
                    to_decimal(weather.get("TEMPERATURAMAXIMAC")),
                    weather.get("CLASSIFICACAOCLIMA", "unknown"),
                    weather.get("PROVEDORCLIMA", "open-meteo"),
                    json.dumps(weather.get("DADOSBRUTOS", {}), ensure_ascii=False),
                ),
            )


def fetch_monitoring_history(
    provider: str,
    station_id: str,
    limit: int = 45,
) -> list[dict[str, Any]]:
    query = """
        SELECT
            g.report_date,
            g.generation_day_kwh,
            g.generation_month_kwh,
            g.inverter_status,
            w.PERCENTUALNUVENS AS "PERCENTUALNUVENS",
            w.CHUVAMM AS "CHUVAMM",
            w.RADIACAOSOLARWHM2 AS "RADIACAOSOLARWHM2",
            w.HORASSOL AS "HORASSOL",
            w.CLASSIFICACAOCLIMA AS "CLASSIFICACAOCLIMA"
        FROM daily_generation g
        INNER JOIN daily_weather w
            ON w.FORNECEDOR = g.provider
           AND w.IDUSINA = g.station_id
           AND w.DATARELATORIO = g.report_date
        WHERE g.provider = %s
          AND g.station_id = %s
        ORDER BY g.report_date DESC
        LIMIT %s;
    """

    with connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, (provider, str(station_id), limit))
            rows = cursor.fetchall()

    return [dict(row) for row in rows]


def fetch_open_maintenance_alert(
    provider: str,
    station_id: str,
    alert_type: str,
) -> dict[str, Any] | None:
    query = """
        SELECT *
        FROM maintenance_alerts
        WHERE provider = %s
          AND station_id = %s
          AND alert_type = %s
          AND status IN ('pending_confirmation', 'confirmed', 'integrator_notified')
        ORDER BY created_at DESC
        LIMIT 1;
    """

    with connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, (provider, str(station_id), alert_type))
            row = cursor.fetchone()

    return dict(row) if row else None


def expire_stale_pending_maintenance_alerts(
    provider: str,
    station_id: str,
    confirmation_days: int,
) -> int:
    query = """
        UPDATE maintenance_alerts
        SET status = 'expired',
            updated_at = NOW()
        WHERE provider = %s
          AND station_id = %s
          AND status = 'pending_confirmation'
          AND created_at < NOW() - (%s * INTERVAL '1 day');
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (provider, str(station_id), confirmation_days))
            return cursor.rowcount


def create_maintenance_alert(
    provider: str,
    station_id: str,
    alert: dict[str, Any],
    status: str = "pending_confirmation",
) -> int:
    query = """
        INSERT INTO maintenance_alerts (
            provider,
            station_id,
            alert_type,
            severity,
            reference_start_date,
            reference_end_date,
            expected_generation_kwh,
            observed_generation_kwh,
            drop_percentage,
            favorable_days_count,
            probable_cause,
            details,
            status,
            created_at,
            updated_at
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s::jsonb, %s, NOW(), NOW()
        )
        RETURNING id;
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (
                    provider,
                    str(station_id),
                    alert["alert_type"],
                    alert.get("severity", "warning"),
                    alert.get("reference_start_date"),
                    alert.get("reference_end_date"),
                    to_decimal(alert.get("expected_generation_kwh")),
                    to_decimal(alert.get("observed_generation_kwh")),
                    to_decimal(alert.get("drop_percentage")),
                    int(alert.get("favorable_days_count", 0)),
                    alert.get("probable_cause", ""),
                    json.dumps(alert.get("details", {}), ensure_ascii=False),
                    status,
                ),
            )
            row = cursor.fetchone()

    return int(row[0])


def confirm_maintenance_alert(
    alert_id: int,
    alert: dict[str, Any],
) -> None:
    query = """
        UPDATE maintenance_alerts
        SET status = 'confirmed',
            severity = %s,
            reference_start_date = %s,
            reference_end_date = %s,
            expected_generation_kwh = %s,
            observed_generation_kwh = %s,
            drop_percentage = %s,
            favorable_days_count = %s,
            probable_cause = %s,
            details = %s::jsonb,
            updated_at = NOW()
        WHERE id = %s
          AND status = 'pending_confirmation';
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (
                    alert.get("severity", "warning"),
                    alert.get("reference_start_date"),
                    alert.get("reference_end_date"),
                    to_decimal(alert.get("expected_generation_kwh")),
                    to_decimal(alert.get("observed_generation_kwh")),
                    to_decimal(alert.get("drop_percentage")),
                    int(alert.get("favorable_days_count", 0)),
                    alert.get("probable_cause", ""),
                    json.dumps(alert.get("details", {}), ensure_ascii=False),
                    alert_id,
                ),
            )


def mark_integrator_notified(alert_id: int) -> None:
    query = """
        UPDATE maintenance_alerts
        SET status = 'integrator_notified',
            integrator_notified_at = COALESCE(integrator_notified_at, NOW()),
            updated_at = NOW()
        WHERE id = %s
          AND status = 'confirmed';
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (alert_id,))


def resolve_other_open_maintenance_alerts(
    provider: str,
    station_id: str,
    current_alert_type: str,
) -> int:
    query = """
        UPDATE maintenance_alerts
        SET status = 'resolved',
            resolved_at = COALESCE(resolved_at, NOW()),
            updated_at = NOW()
        WHERE provider = %s
          AND station_id = %s
          AND alert_type <> %s
          AND status IN ('pending_confirmation', 'confirmed', 'integrator_notified');
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (provider, str(station_id), current_alert_type),
            )
            return cursor.rowcount


def resolve_open_maintenance_alerts(
    provider: str,
    station_id: str,
) -> int:
    query = """
        UPDATE maintenance_alerts
        SET status = 'resolved',
            resolved_at = COALESCE(resolved_at, NOW()),
            updated_at = NOW()
        WHERE provider = %s
          AND station_id = %s
          AND status IN ('pending_confirmation', 'confirmed', 'integrator_notified');
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (provider, str(station_id)))
            return cursor.rowcount

def ensure_growatt_fault_events_table() -> None:
    query = """
        CREATE TABLE IF NOT EXISTS growatt_fault_events (
            id BIGSERIAL PRIMARY KEY,
            station_id TEXT NOT NULL,
            device_sn TEXT NOT NULL,
            fault_code TEXT NOT NULL,
            fault_code_raw TEXT NOT NULL,
            fault_message TEXT,
            fault_time TIMESTAMP NOT NULL,
            recovery_time TIMESTAMP,
            device_type TEXT,
            solution TEXT,
            raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('historical', 'active', 'resolved')),
            normal_checks INTEGER NOT NULL DEFAULT 0,
            last_normal_live_time TIMESTAMP,
            notified_at TIMESTAMPTZ,
            recovery_notified_at TIMESTAMPTZ,
            resolved_at TIMESTAMP,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (device_sn, fault_code_raw, fault_time)
        );

        CREATE INDEX IF NOT EXISTS idx_growatt_fault_events_active
            ON growatt_fault_events (device_sn, status)
            WHERE status = 'active';

        ALTER TABLE growatt_fault_events
            ENABLE ROW LEVEL SECURITY;
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query)


def upsert_growatt_fault_event(
    station_id: str,
    device_sn: str,
    fault_code: str,
    fault_code_raw: str,
    fault_message: str,
    fault_time,
    recovery_time,
    device_type: str,
    solution: str,
    raw_payload: dict[str, Any],
    initial_status: str,
) -> tuple[dict[str, Any], bool]:
    insert_query = """
        INSERT INTO growatt_fault_events (
            station_id,
            device_sn,
            fault_code,
            fault_code_raw,
            fault_message,
            fault_time,
            recovery_time,
            device_type,
            solution,
            raw_payload,
            status,
            resolved_at,
            created_at,
            updated_at
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s::jsonb, %s, %s, NOW(), NOW()
        )
        ON CONFLICT (device_sn, fault_code_raw, fault_time)
        DO NOTHING
        RETURNING *;
    """

    resolved_at = recovery_time if initial_status == "resolved" else None

    with connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                insert_query,
                (
                    str(station_id),
                    str(device_sn),
                    str(fault_code),
                    str(fault_code_raw),
                    fault_message,
                    fault_time,
                    recovery_time,
                    device_type,
                    solution,
                    json.dumps(raw_payload, ensure_ascii=False),
                    initial_status,
                    resolved_at,
                ),
            )
            row = cursor.fetchone()

            if row:
                return dict(row), True

            cursor.execute(
                """
                    UPDATE growatt_fault_events
                    SET
                        recovery_time = COALESCE(%s, recovery_time),
                        fault_message = COALESCE(NULLIF(%s, ''), fault_message),
                        device_type = COALESCE(NULLIF(%s, ''), device_type),
                        solution = COALESCE(NULLIF(%s, ''), solution),
                        raw_payload = %s::jsonb,
                        updated_at = NOW()
                    WHERE device_sn = %s
                      AND fault_code_raw = %s
                      AND fault_time = %s
                    RETURNING *;
                """,
                (
                    recovery_time,
                    fault_message,
                    device_type,
                    solution,
                    json.dumps(raw_payload, ensure_ascii=False),
                    str(device_sn),
                    str(fault_code_raw),
                    fault_time,
                ),
            )
            existing = cursor.fetchone()

    if not existing:
        raise RuntimeError("Não foi possível recuperar o evento Growatt após o upsert.")

    return dict(existing), False


def fetch_active_growatt_faults() -> list[dict[str, Any]]:
    query = """
        SELECT *
        FROM growatt_fault_events
        WHERE status = 'active'
        ORDER BY fault_time ASC;
    """

    with connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query)
            rows = cursor.fetchall()

    return [dict(row) for row in rows]


def fetch_pending_growatt_recovery_notifications() -> list[dict[str, Any]]:
    query = """
        SELECT *
        FROM growatt_fault_events
        WHERE status = 'resolved'
          AND notified_at IS NOT NULL
          AND recovery_notified_at IS NULL
        ORDER BY recovery_time ASC;
    """

    with connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query)
            rows = cursor.fetchall()

    return [dict(row) for row in rows]


def mark_growatt_fault_notified(event_id: int) -> None:
    query = """
        UPDATE growatt_fault_events
        SET notified_at = NOW(),
            updated_at = NOW()
        WHERE id = %s;
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (event_id,))


def mark_growatt_fault_recovery_notified(event_id: int) -> None:
    query = """
        UPDATE growatt_fault_events
        SET recovery_notified_at = NOW(),
            updated_at = NOW()
        WHERE id = %s;
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (event_id,))


def increment_growatt_fault_normal_check(event_id: int, live_time) -> int:
    query = """
        UPDATE growatt_fault_events
        SET normal_checks = normal_checks + 1,
            last_normal_live_time = %s,
            updated_at = NOW()
        WHERE id = %s
          AND status = 'active'
          AND (
              last_normal_live_time IS NULL
              OR last_normal_live_time <> %s
          )
        RETURNING normal_checks;
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (live_time, event_id, live_time),
            )
            row = cursor.fetchone()

            if row:
                return int(row[0])

            cursor.execute(
                """
                    SELECT normal_checks
                    FROM growatt_fault_events
                    WHERE id = %s;
                """,
                (event_id,),
            )
            existing = cursor.fetchone()

    return int(existing[0]) if existing else 0


def reset_growatt_fault_normal_checks() -> None:
    query = """
        UPDATE growatt_fault_events
        SET normal_checks = 0,
            last_normal_live_time = NULL,
            updated_at = NOW()
        WHERE status = 'active'
          AND normal_checks <> 0;
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query)


def mark_growatt_fault_resolved(event_id: int, resolved_at) -> None:
    query = """
        UPDATE growatt_fault_events
        SET status = 'resolved',
            resolved_at = %s,
            recovery_time = COALESCE(recovery_time, %s),
            normal_checks = 0,
            last_normal_live_time = NULL,
            updated_at = NOW()
        WHERE id = %s;
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (resolved_at, resolved_at, event_id),
            )
