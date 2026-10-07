import os
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from config import env
from database import (
    fetch_active_growatt_faults,
    fetch_daily_generation_range,
    fetch_daily_weather_for_date,
    fetch_generation_for_month,
    fetch_open_maintenance_alert,
)
from fault_monitor import ERROR_GUIDANCE_PT, ERROR_MESSAGES_PT, WARNING_MESSAGES_PT
from growatt_client import fetch_generation_history, fetch_growatt_payload
from savings_calculator import (
    calculate_savings_with_fio_b,
    calculate_savings_without_fio_b,
)
from utils import br_number, to_decimal
from weather import get_daily_weather


PROVIDER = "growatt"
REPORT_TIMEZONE = ZoneInfo("America/Bahia")
CONNECTION_TYPE = "monofasico"
MAX_HISTORY_DAYS = 93
MAINTENANCE_ALERT_TYPES = ("inverter_offline", "possible_soiling")


def _iso_or_text(value):
    if value is None:
        return None

    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()

    return str(value)


def _number_or_none(value):
    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_date(value: str, field_name: str = "data") -> date:
    try:
        return date.fromisoformat(str(value or "").strip())
    except ValueError as exc:
        raise ValueError(
            f"{field_name} inválida. Use o formato YYYY-MM-DD."
        ) from exc


def _parse_year_month(value: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = datetime.strptime(text, "%Y-%m")
    except ValueError as exc:
        raise ValueError("Mês inválido. Use o formato YYYY-MM.") from exc

    return parsed.strftime("%Y-%m")


def _station_id() -> str:
    station_id = env("GROWATT_PLANT_ID")
    if station_id:
        return station_id

    station_id = str(fetch_growatt_payload().get("plantId") or "").strip()
    if not station_id:
        raise RuntimeError("Não foi possível identificar a usina.")

    return station_id


def _decimal_env(name: str) -> Decimal | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None

    return to_decimal(raw)


def get_generation_summary() -> dict[str, str]:
    payload = fetch_growatt_payload()

    return {
        "today_kwh": br_number(payload.get("energyTodayKwh"), 1),
        "month_kwh": br_number(payload.get("energyMonthKwh"), 1),
    }


def get_plant_status() -> dict:
    payload = fetch_growatt_payload()

    return {
        "status": str(payload.get("status") or "Sem informação"),
        "power_now_kw": _number_or_none(payload.get("powerNowKw")),
        "energy_today_kwh": _number_or_none(payload.get("energyTodayKwh")),
        "energy_month_kwh": _number_or_none(payload.get("energyMonthKwh")),
        "energy_year_kwh": _number_or_none(payload.get("energyYearKwh")),
        "energy_total_kwh": _number_or_none(payload.get("energyTotalKwh")),
    }


def get_active_faults_summary() -> dict:
    faults = fetch_active_growatt_faults()
    sanitized = []

    for fault in faults[-5:]:
        sanitized.append(
            {
                "code": str(
                    fault.get("fault_code_raw")
                    or fault.get("fault_code")
                    or ""
                ).strip(),
                "message": str(fault.get("fault_message") or "Falha Growatt"),
                "started_at": _iso_or_text(fault.get("fault_time")),
                "status": str(fault.get("status") or "active"),
            }
        )

    return {
        "active_fault_count": len(faults),
        "faults": sanitized,
    }


def get_maintenance_status() -> dict:
    station_id = _station_id()

    alerts = []
    for alert_type in MAINTENANCE_ALERT_TYPES:
        alert = fetch_open_maintenance_alert(
            provider=PROVIDER,
            station_id=station_id,
            alert_type=alert_type,
        )
        if not alert:
            continue

        alerts.append(
            {
                "type": str(alert.get("alert_type") or alert_type),
                "severity": str(alert.get("severity") or "warning"),
                "status": str(alert.get("status") or ""),
                "drop_percentage": _number_or_none(alert.get("drop_percentage")),
                "expected_generation_kwh": _number_or_none(
                    alert.get("expected_generation_kwh")
                ),
                "observed_generation_kwh": _number_or_none(
                    alert.get("observed_generation_kwh")
                ),
                "probable_cause": str(alert.get("probable_cause") or ""),
                "reference_start_date": _iso_or_text(
                    alert.get("reference_start_date")
                ),
                "reference_end_date": _iso_or_text(
                    alert.get("reference_end_date")
                ),
            }
        )

    return {
        "has_open_alert": bool(alerts),
        "alerts": alerts,
    }


def get_generation_period(start_date: str, end_date: str) -> dict:
    start = _parse_date(start_date, "Data inicial")
    end = _parse_date(end_date, "Data final")

    if end < start:
        raise ValueError("A data final não pode ser anterior à data inicial.")

    expected_days = (end - start).days + 1
    if expected_days > MAX_HISTORY_DAYS:
        raise ValueError(
            f"O período máximo por consulta é de {MAX_HISTORY_DAYS} dias."
        )

    today = datetime.now(REPORT_TIMEZONE).date()
    if start > today:
        raise ValueError("Não é possível consultar geração de um período futuro.")

    if end > today:
        end = today
        expected_days = (end - start).days + 1

    station_id = _station_id()
    rows = fetch_daily_generation_range(
        provider=PROVIDER,
        station_id=station_id,
        start_date=start,
        end_date=end,
    )

    by_date = {}
    for row in rows:
        row_date = _iso_or_text(row.get("report_date"))
        if not row_date:
            continue
        by_date[row_date] = {
            "date": row_date,
            "generation_kwh": _number_or_none(row.get("generation_day_kwh")),
            "inverter_status": str(row.get("inverter_status") or ""),
            "source": "monitoring_history",
        }

    expected_dates = {
        (start + timedelta(days=offset)).isoformat()
        for offset in range(expected_days)
    }

    if not expected_dates.issubset(by_date):
        try:
            history = fetch_generation_history(
                start_date=start,
                end_date=end,
                plant_id=station_id,
            )
            for row in history:
                row_date = str(row.get("date") or "")
                if row_date not in expected_dates:
                    continue
                by_date[row_date] = {
                    "date": row_date,
                    "generation_kwh": _number_or_none(row.get("energy_kwh")),
                    "inverter_status": "",
                    "source": "growatt_history",
                }
        except Exception:
            # O banco continua sendo uma fonte válida mesmo se a OpenAPI
            # histórica estiver temporariamente indisponível.
            pass

    if start <= today <= end:
        try:
            live = fetch_growatt_payload()
            by_date[today.isoformat()] = {
                "date": today.isoformat(),
                "generation_kwh": _number_or_none(live.get("energyTodayKwh")),
                "inverter_status": str(live.get("status") or ""),
                "source": "growatt_live",
            }
        except Exception:
            pass

    ordered = [
        by_date[key]
        for key in sorted(by_date)
        if key in expected_dates
    ]
    total = sum(
        item["generation_kwh"] or 0
        for item in ordered
    )
    days_with_data = len(ordered)
    complete = days_with_data == expected_days

    detail_limit = 14
    details = ordered[-detail_limit:]

    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "total_generation_kwh": round(total, 3),
        "days_expected": expected_days,
        "days_with_data": days_with_data,
        "complete_history": complete,
        "missing_days": max(expected_days - days_with_data, 0),
        "daily": details,
        "daily_details_truncated": len(ordered) > detail_limit,
    }


def get_recent_generation(days: int) -> dict:
    try:
        days = int(days)
    except (TypeError, ValueError) as exc:
        raise ValueError("Quantidade de dias inválida.") from exc

    if days < 1 or days > 31:
        raise ValueError("A consulta de últimos dias aceita valores entre 1 e 31.")

    today = datetime.now(REPORT_TIMEZONE).date()
    start = today - timedelta(days=days - 1)
    return get_generation_period(
        start_date=start.isoformat(),
        end_date=today.isoformat(),
    )


def get_monthly_generation(year_month: str) -> dict:
    normalized = _parse_year_month(year_month)
    current_month = datetime.now(REPORT_TIMEZONE).strftime("%Y-%m")
    station_id = _station_id()

    snapshot = fetch_generation_for_month(
        year_month=normalized,
        station_id=station_id,
    )
    snapshot_value = float(snapshot[1]) if snapshot else None

    if normalized != current_month:
        if snapshot_value is None:
            return {
                "available": False,
                "year_month": normalized,
                "reason": "no_monthly_snapshot",
            }

        return {
            "available": True,
            "year_month": normalized,
            "generation_kwh": snapshot_value,
            "source": "monthly_snapshot",
        }

    live_value = None
    try:
        payload = fetch_growatt_payload()
        live_value = _number_or_none(payload.get("energyMonthKwh"))
    except Exception:
        pass

    if live_value is not None and (
        snapshot_value is None or live_value >= snapshot_value
    ):
        return {
            "available": True,
            "year_month": normalized,
            "generation_kwh": live_value,
            "source": "growatt_live",
        }

    if snapshot_value is not None:
        return {
            "available": True,
            "year_month": normalized,
            "generation_kwh": snapshot_value,
            "source": "monthly_snapshot_fallback",
        }

    if live_value is not None:
        return {
            "available": True,
            "year_month": normalized,
            "generation_kwh": live_value,
            "source": "growatt_live",
        }

    return {
        "available": False,
        "year_month": normalized,
        "reason": "current_month_unavailable",
    }

def compare_months(first_year_month: str, second_year_month: str) -> dict:
    first = get_monthly_generation(first_year_month)
    second = get_monthly_generation(second_year_month)

    if not first.get("available") or not second.get("available"):
        return {
            "available": False,
            "first": first,
            "second": second,
            "reason": "missing_monthly_data",
        }

    first_value = float(first.get("generation_kwh") or 0)
    second_value = float(second.get("generation_kwh") or 0)
    difference = second_value - first_value
    percentage = None
    if first_value > 0:
        percentage = (difference / first_value) * 100

    return {
        "available": True,
        "first": first,
        "second": second,
        "difference_kwh": round(difference, 3),
        "difference_percent_relative_to_first": (
            round(percentage, 2)
            if percentage is not None
            else None
        ),
        "higher_month": (
            second["year_month"]
            if second_value > first_value
            else first["year_month"]
            if first_value > second_value
            else "equal"
        ),
    }


def get_savings_summary(year_month: str) -> dict:
    monthly = get_monthly_generation(year_month)
    if not monthly.get("available"):
        return {
            "available": False,
            "year_month": monthly.get("year_month"),
            "reason": "generation_unavailable",
        }

    tariff = _decimal_env("ENERGY_TARIFF")
    if tariff is None or tariff <= 0:
        return {
            "available": False,
            "year_month": monthly["year_month"],
            "reason": "tariff_not_configured",
        }

    generation = to_decimal(monthly.get("generation_kwh"))
    average_consumption = _decimal_env("AVERAGE_CONSUMPTION_KWH")
    if average_consumption is None:
        average_consumption = generation + Decimal("30")

    tusd_fio_b = _decimal_env("TUSD_FIO_B_KWH")
    fio_b_percentage = _decimal_env("FIO_B_PERCENTAGE")

    if tusd_fio_b is not None and fio_b_percentage is not None:
        calculation = calculate_savings_with_fio_b(
            generation_month_kwh=generation,
            average_consumption_month_kwh=average_consumption,
            final_tariff_kwh=tariff,
            tusd_fio_b_kwh=tusd_fio_b,
            fio_b_percentage=fio_b_percentage,
            connection_type=CONNECTION_TYPE,
        )
        mode = "com_fio_b"
    else:
        calculation = calculate_savings_without_fio_b(
            generation_month_kwh=generation,
            average_consumption_month_kwh=average_consumption,
            final_tariff_kwh=tariff,
            connection_type=CONNECTION_TYPE,
        )
        mode = "estimativa_sem_fio_b"

    return {
        "available": True,
        "year_month": monthly["year_month"],
        "generation_kwh": float(generation),
        "estimated_savings_brl": float(calculation["estimated_savings"]),
        "compensated_energy_kwh": float(calculation["compensated_energy_kwh"]),
        "generated_credits_kwh": float(calculation["generated_credits_kwh"]),
        "calculation_mode": mode,
    }


def _sanitize_weather(data: dict, report_date: date, source: str) -> dict:
    return {
        "available": True,
        "date": report_date.isoformat(),
        "cloud_cover_percent": _number_or_none(
            data.get("cloud_cover_percent", data.get("PERCENTUALNUVENS"))
        ),
        "rainfall_mm": _number_or_none(
            data.get("rainfall_mm", data.get("CHUVAMM"))
        ),
        "solar_radiation_wh_m2": _number_or_none(
            data.get("solar_radiation_wh_m2", data.get("RADIACAOSOLARWHM2"))
        ),
        "sunshine_hours": _number_or_none(
            data.get("sunshine_hours", data.get("HORASSOL"))
        ),
        "temperature_min_c": _number_or_none(
            data.get("temperature_min_c", data.get("TEMPERATURAMINIMAC"))
        ),
        "temperature_max_c": _number_or_none(
            data.get("temperature_max_c", data.get("TEMPERATURAMAXIMAC"))
        ),
        "weather_class": str(
            data.get("weather_class", data.get("CLASSIFICACAOCLIMA")) or "unknown"
        ),
        "source": source,
    }


def get_weather_summary(report_date: str) -> dict:
    parsed_date = _parse_date(report_date, "Data do clima")
    today = datetime.now(REPORT_TIMEZONE).date()

    if parsed_date > today:
        raise ValueError("Não é possível consultar clima futuro nesta ferramenta.")

    station_id = _station_id()
    stored = fetch_daily_weather_for_date(
        provider=PROVIDER,
        station_id=station_id,
        report_date=parsed_date,
    )
    if stored:
        return _sanitize_weather(stored, parsed_date, "monitoring_history")

    latitude_raw = env("STATION_LATITUDE")
    longitude_raw = env("STATION_LONGITUDE")
    if not latitude_raw or not longitude_raw:
        return {
            "available": False,
            "date": parsed_date.isoformat(),
            "reason": "station_location_not_configured",
        }

    try:
        weather = get_daily_weather(
            latitude=float(latitude_raw.replace(",", ".")),
            longitude=float(longitude_raw.replace(",", ".")),
            report_date=parsed_date,
        )
    except Exception:
        return {
            "available": False,
            "date": parsed_date.isoformat(),
            "reason": "open_meteo_unavailable",
        }

    return _sanitize_weather(weather, parsed_date, "open_meteo")

def get_fault_code_info(code: str) -> dict:
    normalized = str(code or "").strip()
    normalized = normalized.split("(", 1)[0].strip()
    if not normalized:
        raise ValueError("Código de falha vazio.")

    error_message = ERROR_MESSAGES_PT.get(normalized)
    warning_message = WARNING_MESSAGES_PT.get(normalized)
    guidance = ERROR_GUIDANCE_PT.get(normalized)

    if not error_message and not warning_message:
        return {
            "known": False,
            "code": normalized,
        }

    return {
        "known": True,
        "code": normalized,
        "error_meaning": error_message,
        "warning_meaning": warning_message,
        "guidance": guidance,
    }


def _safe_component(callback, *args) -> dict:
    try:
        return {
            "available": True,
            "data": callback(*args),
        }
    except Exception:
        return {
            "available": False,
        }


def get_comprehensive_analysis() -> dict:
    now = datetime.now(REPORT_TIMEZONE)
    current_month = now.strftime("%Y-%m")

    return {
        "generated_at": now.isoformat(),
        "plant": _safe_component(get_plant_status),
        "active_faults": _safe_component(get_active_faults_summary),
        "maintenance": _safe_component(get_maintenance_status),
        "weather_today": _safe_component(
            get_weather_summary,
            now.date().isoformat(),
        ),
        "savings_current_month": _safe_component(
            get_savings_summary,
            current_month,
        ),
    }
