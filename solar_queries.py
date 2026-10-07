from config import env
from database import fetch_active_growatt_faults, fetch_open_maintenance_alert
from growatt_client import fetch_growatt_payload
from utils import br_number


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
    station_id = env("GROWATT_PLANT_ID")
    if not station_id:
        station_id = str(fetch_growatt_payload().get("plantId") or "").strip()

    alerts = []
    for alert_type in MAINTENANCE_ALERT_TYPES:
        alert = fetch_open_maintenance_alert(
            provider="growatt",
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
