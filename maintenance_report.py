import os
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from database import (
    create_maintenance_alert,
    fetch_monitoring_history,
    has_open_maintenance_alert,
    mark_integrator_notified,
    save_daily_generation,
    save_daily_weather,
)
from maintenance import analyze_maintenance_need
from send_daily_report import env, fetch_growatt_payload, send_whatsapp_to
from weather import get_daily_weather


REPORT_TIMEZONE = ZoneInfo("America/Bahia")
PROVIDER = "growatt"


def decimal_env(name: str, default: str) -> Decimal:
    value = os.getenv(name, default).strip().replace(",", ".")
    return Decimal(value)


def integer_env(name: str, default: int) -> int:
    value = os.getenv(name, str(default)).strip()
    return int(value)


def format_number(value, decimals: int = 1) -> str:
    return (
        f"{float(value):,.{decimals}f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


def build_integrator_alert_message(alert: dict, station_id: str) -> str:
    return f"""🔎 SolCare — Análise preventiva de desempenho

Usina: {station_id}

Foi identificada uma redução persistente de {format_number(alert['drop_percentage'], 1)}% em dias com condições climáticas favoráveis.

Geração diária esperada: {format_number(alert['expected_generation_kwh'], 1)} kWh
Geração diária observada: {format_number(alert['observed_generation_kwh'], 1)} kWh
Dias favoráveis analisados: {alert['favorable_days_count']}

Possíveis causas:
{alert['probable_cause']}

Recomendamos uma inspeção técnica antes de qualquer intervenção ou contato com o cliente.
"""


def main() -> None:
    now = datetime.now(REPORT_TIMEZONE)
    report_date = now.date()

    latitude = float(env("STATION_LATITUDE", required=True).replace(",", "."))
    longitude = float(env("STATION_LONGITUDE", required=True).replace(",", "."))

    payload = fetch_growatt_payload()
    station_id = str(payload.get("plantId") or "").strip()

    if not station_id:
        raise RuntimeError("Não foi possível identificar o Plant ID da Growatt.")

    save_daily_generation(
        provider=PROVIDER,
        station_id=station_id,
        report_date=report_date,
        generation_day_kwh=payload.get("energyTodayKwh", 0),
        generation_month_kwh=payload.get("energyMonthKwh", 0),
        inverter_status=str(payload.get("status") or ""),
        device_sn=str(payload.get("deviceSn") or ""),
    )

    weather = get_daily_weather(
        latitude=latitude,
        longitude=longitude,
        report_date=report_date,
    )

    save_daily_weather(
        provider=PROVIDER,
        station_id=station_id,
        report_date=report_date,
        latitude=latitude,
        longitude=longitude,
        weather=weather,
    )

    print(
        "Monitoramento diário salvo:",
        {
            "station_id": station_id,
            "report_date": report_date.isoformat(),
            "generation_day_kwh": payload.get("energyTodayKwh", 0),
            "generation_month_kwh": payload.get("energyMonthKwh", 0),
            "CLASSIFICACAOCLIMA": weather.get("CLASSIFICACAOCLIMA"),
            "RADIACAOSOLARWHM2": weather.get("RADIACAOSOLARWHM2"),
            "CHUVAMM": weather.get("CHUVAMM"),
        },
    )

    history = fetch_monitoring_history(
        provider=PROVIDER,
        station_id=station_id,
        limit=integer_env("MAINTENANCE_HISTORY_DAYS", 45),
    )

    analysis = analyze_maintenance_need(
        history=history,
        minimum_recent_days=integer_env("MAINTENANCE_RECENT_DAYS", 4),
        minimum_baseline_days=integer_env("MAINTENANCE_BASELINE_DAYS", 7),
        drop_threshold_percent=decimal_env("MAINTENANCE_DROP_PERCENT", "25"),
        minimum_radiation_wh_m2=decimal_env(
            "MAINTENANCE_MIN_RADIATION_WH_M2", "3000"
        ),
    )

    print("Resultado da análise de manutenção:", analysis)

    if not analysis.get("alert"):
        print("Nenhum alerta de manutenção será criado nesta execução.")
        return

    alert_type = str(analysis["alert_type"])

    if has_open_maintenance_alert(
        provider=PROVIDER,
        station_id=station_id,
        alert_type=alert_type,
    ):
        print("Já existe um alerta aberto desse tipo para esta usina.")
        return

    alert_id = create_maintenance_alert(
        provider=PROVIDER,
        station_id=station_id,
        alert=analysis,
    )

    message = build_integrator_alert_message(analysis, station_id)
    print("Mensagem de alerta para a integradora:")
    print(message)

    send_whatsapp_to(
        env("WHATSAPP_PHONE", required=True),
        env("WHATSAPP_APIKEY", required=True),
        message,
    )

    mark_integrator_notified(alert_id)
    print(f"Alerta {alert_id} criado e enviado para a integradora.")


if __name__ == "__main__":
    main()
