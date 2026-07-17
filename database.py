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
            provider,
            station_id,
            report_date,
            latitude,
            longitude,
            cloud_cover_percent,
            rainfall_mm,
            solar_radiation_wh_m2,
            sunshine_duration_hours,
            temperature_min_c,
            temperature_max_c,
            weather_class,
            weather_provider,
            raw_payload,
            collected_at,
            updated_at
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s::jsonb, NOW(), NOW()
        )
        ON CONFLICT (provider, station_id, report_date)
        DO UPDATE SET
            latitude = EXCLUDED.latitude,
            longitude = EXCLUDED.longitude,
            cloud_cover_percent = EXCLUDED.cloud_cover_percent,
            rainfall_mm = EXCLUDED.rainfall_mm,
            solar_radiation_wh_m2 = EXCLUDED.solar_radiation_wh_m2,
            sunshine_duration_hours = EXCLUDED.sunshine_duration_hours,
            temperature_min_c = EXCLUDED.temperature_min_c,
            temperature_max_c = EXCLUDED.temperature_max_c,
            weather_class = EXCLUDED.weather_class,
            weather_provider = EXCLUDED.weather_provider,
            raw_payload = EXCLUDED.raw_payload,
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
                    to_decimal(latitude),
                    to_decimal(longitude),
                    to_decimal(weather.get("cloud_cover_percent")),
                    to_decimal(weather.get("rainfall_mm")),
                    to_decimal(weather.get("solar_radiation_wh_m2")),
                    to_decimal(weather.get("sunshine_duration_hours")),
                    to_decimal(weather.get("temperature_min_c")),
                    to_decimal(weather.get("temperature_max_c")),
                    weather.get("weather_class", "unknown"),
                    weather.get("weather_provider", "open-meteo"),
                    json.dumps(weather.get("raw_payload", {}), ensure_ascii=False),
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
            w.cloud_cover_percent,
            w.rainfall_mm,
            w.solar_radiation_wh_m2,
            w.sunshine_duration_hours,
            w.weather_class
        FROM daily_generation g
        INNER JOIN daily_weather w
            ON w.provider = g.provider
           AND w.station_id = g.station_id
           AND w.report_date = g.report_date
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


def has_open_maintenance_alert(
    provider: str,
    station_id: str,
    alert_type: str,
) -> bool:
    query = """
        SELECT 1
        FROM maintenance_alerts
        WHERE provider = %s
          AND station_id = %s
          AND alert_type = %s
          AND status IN ('pending_confirmation', 'confirmed', 'approved', 'customer_notified')
        LIMIT 1;
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (provider, str(station_id), alert_type))
            return cursor.fetchone() is not None


def fetch_recent_pending_alert(
    provider: str,
    station_id: str,
    alert_type: str,
    within_days: int = 5,
) -> dict[str, Any] | None:
    # busca um alerta pending_confirmation recente do mesmo tipo, pra
    # decidir se essa detecção confirma um alerta anterior
    cutoff = (date.today() - timedelta(days=within_days)).isoformat()

    query = """
        SELECT id, created_at, drop_percentage
        FROM maintenance_alerts
        WHERE provider = %s
          AND station_id = %s
          AND alert_type = %s
          AND status = 'pending_confirmation'
          AND created_at >= %s
        ORDER BY created_at DESC
        LIMIT 1;
    """

    with connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, (provider, str(station_id), alert_type, cutoff))
            row = cursor.fetchone()

    return dict(row) if row else None


def create_maintenance_alert(
    provider: str,
    station_id: str,
    alert: dict[str, Any],
    status: str = "pending_confirmation",
) -> int:
    # nasce como pending_confirmation; só vira confirmed se a mesma
    # condição aparecer de novo na execução seguinte
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


def confirm_maintenance_alert(alert_id: int) -> None:
    query = """
        UPDATE maintenance_alerts
        SET status = 'confirmed',
            updated_at = NOW()
        WHERE id = %s;
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (alert_id,))


def mark_integrator_notified(alert_id: int) -> None:
    query = """
        UPDATE maintenance_alerts
        SET status = 'customer_notified',
            integrator_notified_at = NOW(),
            updated_at = NOW()
        WHERE id = %s;
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (alert_id,))
