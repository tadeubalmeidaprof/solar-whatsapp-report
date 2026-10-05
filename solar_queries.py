from growatt_client import fetch_growatt_payload
from utils import br_number


def get_generation_summary() -> dict[str, str]:
    payload = fetch_growatt_payload()

    return {
        "today_kwh": br_number(payload.get("energyTodayKwh"), 1),
        "month_kwh": br_number(payload.get("energyMonthKwh"), 1),
    }
