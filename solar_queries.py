import logging
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
from maintenance import analyze_operational_performance
from growatt_client import (
    fetch_generation_history,
    fetch_growatt_payload,
    fetch_plant_peak_power_kwp,
    fetch_plant_power_curve,
)
from savings_calculator import (
    calculate_savings_with_fio_b,
    calculate_savings_without_fio_b,
)
from utils import br_number, to_decimal
from weather import get_daily_weather, get_weather_window


PROVIDER = "growatt"
REPORT_TIMEZONE = ZoneInfo("America/Bahia")
CONNECTION_TYPE = "monofasico"
MAX_HISTORY_DAYS = 93
MAINTENANCE_ALERT_TYPES = ("inverter_offline", "possible_soiling")

logger = logging.getLogger(__name__)


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
    first_normalized = _parse_year_month(first_year_month)
    second_normalized = _parse_year_month(second_year_month)

    first = get_monthly_generation(first_normalized)
    second = get_monthly_generation(second_normalized)

    if not first.get("available") or not second.get("available"):
        return {
            "available": False,
            "first": first,
            "second": second,
            "reason": "missing_monthly_data",
        }

    now = datetime.now(REPORT_TIMEZONE)
    current_month = now.strftime("%Y-%m")
    current_day = now.day

    first_value = float(first.get("generation_kwh") or 0)
    second_value = float(second.get("generation_kwh") or 0)
    raw_difference = second_value - first_value
    raw_percentage = None
    if first_value > 0:
        raw_percentage = (raw_difference / first_value) * 100

    result = {
        "available": True,
        "first": first,
        "second": second,
        "raw_difference_kwh": round(raw_difference, 3),
        "raw_difference_percent_relative_to_first": (
            round(raw_percentage, 2)
            if raw_percentage is not None
            else None
        ),
        "comparison_mode": "full_months",
        "months_are_directly_comparable": True,
    }

    current_in_comparison = (
        first_normalized == current_month
        or second_normalized == current_month
    )

    if not current_in_comparison:
        result.update(
            {
                "difference_kwh": round(raw_difference, 3),
                "difference_percent_relative_to_first": (
                    round(raw_percentage, 2)
                    if raw_percentage is not None
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
        )
        return result

    result["comparison_mode"] = "equivalent_partial_period"
    result["months_are_directly_comparable"] = False
    result["partial_month"] = current_month
    result["partial_month_through_day"] = current_day
    result["warning"] = (
        "Um dos meses ainda está em andamento. Os totais mensais brutos "
        "não devem ser comparados diretamente."
    )

    def equivalent_period(year_month: str) -> dict:
        parsed = datetime.strptime(year_month, "%Y-%m")
        start = date(parsed.year, parsed.month, 1)
        end = date(parsed.year, parsed.month, current_day)
        return get_generation_period(
            start_date=start.isoformat(),
            end_date=end.isoformat(),
        )

    try:
        first_period = equivalent_period(first_normalized)
        second_period = equivalent_period(second_normalized)
    except (ValueError, RuntimeError):
        return result

    if (
        not first_period.get("complete_history")
        or not second_period.get("complete_history")
    ):
        result["fair_comparison_available"] = False
        result["fair_comparison"] = {
            "through_day": current_day,
            "first_period": first_period,
            "second_period": second_period,
        }
        return result

    first_partial = float(first_period.get("total_generation_kwh") or 0)
    second_partial = float(second_period.get("total_generation_kwh") or 0)
    fair_difference = second_partial - first_partial
    fair_percentage = None
    if first_partial > 0:
        fair_percentage = (fair_difference / first_partial) * 100

    result["fair_comparison_available"] = True
    result["fair_comparison"] = {
        "through_day": current_day,
        "first_year_month": first_normalized,
        "second_year_month": second_normalized,
        "first_generation_kwh": first_partial,
        "second_generation_kwh": second_partial,
        "difference_kwh": round(fair_difference, 3),
        "difference_percent_relative_to_first": (
            round(fair_percentage, 2)
            if fair_percentage is not None
            else None
        ),
        "higher_period": (
            second_normalized
            if second_partial > first_partial
            else first_normalized
            if first_partial > second_partial
            else "equal"
        ),
    }
    return result

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
    except Exception as exc:
        logger.warning(
            "Falha ao consultar Open-Meteo para %s: %s",
            parsed_date.isoformat(),
            exc,
        )
        return {
            "available": False,
            "date": parsed_date.isoformat(),
            "reason": "open_meteo_unavailable",
        }

    return _sanitize_weather(weather, parsed_date, "open_meteo")


def get_weather_window_summary(
    report_date: str,
    start_hour: int = 7,
    end_hour: int = 17,
) -> dict:
    parsed_date = _parse_date(report_date, "Data do clima")
    today = datetime.now(REPORT_TIMEZONE).date()

    if parsed_date > today:
        raise ValueError("Não é possível consultar clima futuro nesta ferramenta.")

    latitude_raw = env("STATION_LATITUDE")
    longitude_raw = env("STATION_LONGITUDE")
    if not latitude_raw or not longitude_raw:
        return {
            "available": False,
            "date": parsed_date.isoformat(),
            "reason": "station_location_not_configured",
        }

    try:
        return get_weather_window(
            latitude=float(latitude_raw.replace(",", ".")),
            longitude=float(longitude_raw.replace(",", ".")),
            report_date=parsed_date,
            start_hour=start_hour,
            end_hour=end_hour,
        )
    except Exception as exc:
        logger.warning(
            "Falha ao consultar clima horário para %s (%s-%s): %s",
            parsed_date.isoformat(),
            start_hour,
            end_hour,
            exc,
        )
        return {
            "available": False,
            "date": parsed_date.isoformat(),
            "start_hour": start_hour,
            "end_hour": end_hour,
            "reason": "weather_provider_unavailable",
        }


def _parse_power_timestamp(value: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Timestamp vazio na curva de potência.")

    if text.endswith("Z"):
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    else:
        parsed = datetime.fromisoformat(text.replace(" ", "T"))

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=REPORT_TIMEZONE)

    return parsed.astimezone(REPORT_TIMEZONE)



def _summarize_power_curve(
    rows: list[dict],
    peak_power_kwp: float | None,
    parsed_date: date,
    start_hour: int,
    end_hour: int,
) -> dict:
    points = []
    for row in rows:
        try:
            timestamp = _parse_power_timestamp(row.get("time"))
        except (TypeError, ValueError):
            continue

        if timestamp.date() != parsed_date:
            continue

        if not start_hour <= timestamp.hour < end_hour:
            continue

        power_w = _number_or_none(row.get("power_w"))
        if power_w is None:
            continue

        points.append(
            {
                "timestamp": timestamp,
                "power_w": max(power_w, 0.0),
            }
        )

    points.sort(key=lambda item: item["timestamp"])

    if not points:
        return {
            "available": False,
            "date": parsed_date.isoformat(),
            "start_hour": start_hour,
            "end_hour": end_hour,
            "reason": "growatt_power_curve_unavailable",
        }

    meaningful_threshold_w = 50.0
    if peak_power_kwp and peak_power_kwp > 0:
        meaningful_threshold_w = max(
            50.0,
            peak_power_kwp * 1000 * 0.05,
        )

    active_seconds = 0.0
    estimated_energy_kwh = 0.0
    significant_points = []
    peak_observed_w = 0.0

    for index, point in enumerate(points):
        power_w = point["power_w"]
        peak_observed_w = max(peak_observed_w, power_w)

        if index + 1 < len(points):
            delta_seconds = (
                points[index + 1]["timestamp"] - point["timestamp"]
            ).total_seconds()
            if delta_seconds <= 0 or delta_seconds > 600:
                delta_seconds = 300.0
        else:
            delta_seconds = 300.0

        estimated_energy_kwh += (
            power_w * (delta_seconds / 3600)
        ) / 1000

        if power_w >= meaningful_threshold_w:
            active_seconds += delta_seconds
            significant_points.append(point)

    active_hours = active_seconds / 3600
    equivalent_full_power_hours = None
    average_power_fraction = None

    if peak_power_kwp and peak_power_kwp > 0:
        equivalent_full_power_hours = (
            estimated_energy_kwh / peak_power_kwp
        )
        if active_hours > 0:
            average_power_fraction = (
                equivalent_full_power_hours / active_hours
            )

    generation_start = None
    generation_end = None
    if significant_points:
        generation_start = significant_points[0]["timestamp"].strftime("%H:%M")
        generation_end = significant_points[-1]["timestamp"].strftime("%H:%M")

    return {
        "available": True,
        "date": parsed_date.isoformat(),
        "window_label": f"{start_hour:02d}:00-{end_hour:02d}:00",
        "source": "growatt_power_curve",
        "samples": len(points),
        "plant_peak_power_kwp": (
            round(peak_power_kwp, 3)
            if peak_power_kwp is not None
            else None
        ),
        "meaningful_generation_threshold_w": round(
            meaningful_threshold_w,
            1,
        ),
        "active_generation_hours": round(active_hours, 2),
        "equivalent_full_power_hours": (
            round(equivalent_full_power_hours, 2)
            if equivalent_full_power_hours is not None
            else None
        ),
        "average_power_fraction_of_peak": (
            round(average_power_fraction, 4)
            if average_power_fraction is not None
            else None
        ),
        "estimated_energy_in_window_kwh": round(
            estimated_energy_kwh,
            2,
        ),
        "peak_observed_kw": round(peak_observed_w / 1000, 3),
        "generation_start": generation_start,
        "generation_end": generation_end,
        "metric_explanation": (
            "active_generation_hours mede por quanto tempo a usina ficou acima "
            "de 5% da potência pico (mínimo 50 W). "
            "equivalent_full_power_hours representa a energia da janela dividida "
            "pela potência pico instalada. "
            "average_power_fraction_of_peak mede a potência média durante as "
            "horas produtivas como fração da potência pico. "
            "Nenhuma dessas métricas é duração meteorológica oficial de insolação."
        ),
    }


def get_solar_generation_hours(
    report_date: str,
    start_hour: int = 7,
    end_hour: int = 17,
) -> dict:
    parsed_date = _parse_date(report_date, "Data da geração")
    today = datetime.now(REPORT_TIMEZONE).date()

    if parsed_date > today:
        raise ValueError("Não é possível consultar geração de um dia futuro.")

    try:
        start_hour = int(start_hour)
        end_hour = int(end_hour)
    except (TypeError, ValueError) as exc:
        raise ValueError("Horários devem ser números inteiros.") from exc

    if not 0 <= start_hour <= 23 or not 1 <= end_hour <= 24:
        raise ValueError("Horários devem estar entre 0 e 24.")

    if end_hour <= start_hour:
        raise ValueError("O horário final deve ser posterior ao inicial.")

    station_id = _station_id()
    peak_power_kwp = fetch_plant_peak_power_kwp(
        plant_id=station_id,
    )
    rows = fetch_plant_power_curve(
        report_date=parsed_date,
        plant_id=station_id,
    )

    return _summarize_power_curve(
        rows=rows,
        peak_power_kwp=peak_power_kwp,
        parsed_date=parsed_date,
        start_hour=start_hour,
        end_hour=end_hour,
    )


def get_performance_diagnostic(
    report_date: str,
    start_hour: int = 7,
    end_hour: int = 17,
) -> dict:
    parsed_date = _parse_date(report_date, "Data do diagnóstico")
    now = datetime.now(REPORT_TIMEZONE)
    today = now.date()

    if parsed_date > today:
        raise ValueError("Não é possível diagnosticar um dia futuro.")

    try:
        start_hour = int(start_hour)
        end_hour = int(end_hour)
    except (TypeError, ValueError) as exc:
        raise ValueError("Horários devem ser números inteiros.") from exc

    if not 0 <= start_hour <= 23 or not 1 <= end_hour <= 24:
        raise ValueError("Horários devem estar entre 0 e 24.")

    if end_hour <= start_hour:
        raise ValueError("O horário final deve ser posterior ao inicial.")

    if parsed_date == today:
        settle_time = datetime(
            today.year,
            today.month,
            today.day,
            end_hour % 24,
            20,
            tzinfo=REPORT_TIMEZONE,
        )
        if end_hour == 24:
            settle_time = datetime(
                today.year,
                today.month,
                today.day,
                23,
                59,
                tzinfo=REPORT_TIMEZONE,
            )

        if now < settle_time:
            return {
                "available": False,
                "date": parsed_date.isoformat(),
                "status": "inconclusive_window_in_progress",
                "window_label": f"{start_hour:02d}:00-{end_hour:02d}:00",
                "available_after": settle_time.isoformat(),
                "reason": (
                    "A janela solar ainda não terminou. "
                    "O SolCare não conclui manutenção com dados parciais."
                ),
            }

    station_id = _station_id()
    peak_power_kwp = fetch_plant_peak_power_kwp(
        plant_id=station_id,
    )
    if peak_power_kwp is None or peak_power_kwp <= 0:
        return {
            "available": False,
            "date": parsed_date.isoformat(),
            "status": "inconclusive_missing_peak_power",
            "reason": "Potência pico da usina indisponível.",
        }

    current_rows = fetch_plant_power_curve(
        report_date=parsed_date,
        plant_id=station_id,
    )
    current_metric = _summarize_power_curve(
        rows=current_rows,
        peak_power_kwp=peak_power_kwp,
        parsed_date=parsed_date,
        start_hour=start_hour,
        end_hour=end_hour,
    )

    if not current_metric.get("available"):
        return {
            "available": False,
            "date": parsed_date.isoformat(),
            "status": "inconclusive_missing_power_curve",
            "reason": "Curva de potência do dia indisponível.",
        }

    historical_metrics = []
    for offset in range(1, 11):
        historical_date = parsed_date - timedelta(days=offset)
        try:
            rows = fetch_plant_power_curve(
                report_date=historical_date,
                plant_id=station_id,
            )
            metric = _summarize_power_curve(
                rows=rows,
                peak_power_kwp=peak_power_kwp,
                parsed_date=historical_date,
                start_hour=start_hour,
                end_hour=end_hour,
            )
        except Exception as exc:
            logger.warning(
                "Falha ao obter curva histórica para diagnóstico em %s: %s",
                historical_date.isoformat(),
                exc,
            )
            continue

        if metric.get("available"):
            historical_metrics.append(metric)

        if len(historical_metrics) >= 8:
            break

    weather = get_weather_window_summary(
        report_date=parsed_date.isoformat(),
        start_hour=start_hour,
        end_hour=end_hour,
    )

    daily_generation_kwh = None
    try:
        generation = get_generation_period(
            start_date=parsed_date.isoformat(),
            end_date=parsed_date.isoformat(),
        )
        if generation.get("days_with_data"):
            daily_generation_kwh = _number_or_none(
                generation.get("total_generation_kwh")
            )
    except Exception as exc:
        logger.warning(
            "Falha ao consultar geração diária no diagnóstico: %s",
            exc,
        )

    active_fault_count = None
    existing_maintenance_alert = False
    if parsed_date == today:
        try:
            active_fault_count = int(
                get_active_faults_summary().get(
                    "active_fault_count",
                    0,
                )
            )
        except Exception:
            active_fault_count = None

        try:
            existing_maintenance_alert = bool(
                get_maintenance_status().get("has_open_alert")
            )
        except Exception:
            existing_maintenance_alert = False

    weather_for_analysis = (
        weather
        if weather.get("available")
        else {}
    )

    diagnostic = analyze_operational_performance(
        current_metric=current_metric,
        historical_metrics=historical_metrics,
        weather=weather_for_analysis,
        active_fault_count=active_fault_count,
        existing_maintenance_alert=existing_maintenance_alert,
    )

    return {
        "available": True,
        "date": parsed_date.isoformat(),
        "window_label": current_metric.get("window_label"),
        "daily_generation_kwh": daily_generation_kwh,
        "operational_metric": {
            "active_generation_hours": current_metric.get(
                "active_generation_hours"
            ),
            "equivalent_full_power_hours": current_metric.get(
                "equivalent_full_power_hours"
            ),
            "average_power_fraction_of_peak": current_metric.get(
                "average_power_fraction_of_peak"
            ),
            "estimated_energy_in_window_kwh": current_metric.get(
                "estimated_energy_in_window_kwh"
            ),
            "plant_peak_power_kwp": current_metric.get(
                "plant_peak_power_kwp"
            ),
            "peak_observed_kw": current_metric.get(
                "peak_observed_kw"
            ),
            "generation_start": current_metric.get(
                "generation_start"
            ),
            "generation_end": current_metric.get(
                "generation_end"
            ),
        },
        "weather": {
            "available": bool(weather.get("available")),
            "source": weather.get("source"),
            "average_cloud_cover_percent": weather.get(
                "average_cloud_cover_percent"
            ),
            "total_precipitation_mm": weather.get(
                "total_precipitation_mm"
            ),
            "average_temperature_c": weather.get(
                "average_temperature_c"
            ),
            "max_temperature_c": weather.get(
                "max_temperature_c"
            ),
        },
        "historical_days_found": len(historical_metrics),
        "active_fault_count": active_fault_count,
        "existing_maintenance_alert": existing_maintenance_alert,
        "diagnostic": diagnostic,
        "interpretation_limits": [
            (
                "Horas equivalentes da Growatt são uma métrica operacional, "
                "não HSP meteorológica oficial."
            ),
            (
                "Temperatura ambiente é contexto. Sem temperatura do módulo e "
                "coeficiente térmico do painel, não é aplicada correção térmica exata."
            ),
            (
                "Manutenção só é suspeitada com histórico suficiente e "
                "evidência persistente; um único dia ruim gera no máximo atenção."
            ),
        ],
    }

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
