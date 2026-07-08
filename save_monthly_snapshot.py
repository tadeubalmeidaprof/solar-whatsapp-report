from datetime import datetime
from zoneinfo import ZoneInfo

from database import save_monthly_generation_snapshot
from send_daily_report import fetch_growatt_payload


REPORT_TIMEZONE = ZoneInfo("America/Bahia")


def main():
    payload = fetch_growatt_payload()

    station_id = str(payload.get("plantId") or "").strip()
    generation_month_kwh = float(payload.get("energyMonthKwh") or 0)
    today = datetime.now(REPORT_TIMEZONE).date()

    if not station_id:
        raise RuntimeError("Não foi possível identificar o plantId da Growatt.")

    save_monthly_generation_snapshot(
        station_id=station_id,
        report_date=today,
        generation_kwh=generation_month_kwh,
    )

    print(
        "Snapshot mensal salvo no Supabase:",
        {
            "station_id": station_id,
            "report_date": today.isoformat(),
            "generation_month_kwh": generation_month_kwh,
        },
    )


if __name__ == "__main__":
    main()
