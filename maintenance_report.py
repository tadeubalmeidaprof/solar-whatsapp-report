import os
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from database import (
    confirm_maintenance_alert,
    create_maintenance_alert,
    expire_stale_pending_maintenance_alerts,
    fetch_monitoring_history,
    fetch_open_maintenance_alert,
    mark_integrator_notified,
    resolve_open_maintenance_alerts,
    save_daily_generation,
    save_daily_weather,
)
from maintenance import analyze_maintenance_need
from send_daily_report import env, fetch_growatt_payload, send_whatsapp_to
from weather import get_daily_weather


REPORT_TIMEZONE = ZoneInfo("America/Bahia")
PROVIDER = "growatt"


def decimal_env(name: str, default: str) -> Decimal:
    value = os.getenv(name, "").strip() or default
    return Decimal(value.replace(",", "."))


def integer_env(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    return int(value or default)


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

    confirmation_days = integer_env("MAINTENANCE_CONFIRMATION_DAYS", 5)
    expired_count = expire_stale_pending_maintenance_alerts(
        provider=PROVIDER,
        station_id=station_id,
        confirmation_days=confirmation_days,
    )
    if expired_count:
        print(f"Alertas pendentes expirados: {expired_count}")

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
        if analysis.get("reason") == "drop_below_threshold":
            resolved_count = resolve_open_maintenance_alerts(
                provider=PROVIDER,
                station_id=station_id,
            )
            if resolved_count:
                print(f"Alertas de manutenção resolvidos: {resolved_count}")

        print("Nenhum novo alerta de manutenção nesta execução.")
        return

    alert_type = str(analysis["alert_type"])
    existing_alert = fetch_open_maintenance_alert(
        provider=PROVIDER,
        station_id=station_id,
        alert_type=alert_type,
    )

    if not existing_alert:
        alert_id = create_maintenance_alert(
            provider=PROVIDER,
            station_id=station_id,
            alert=analysis,
        )
        print(
            f"Alerta {alert_id} criado como pending_confirmation; "
            "aguardando nova detecção antes de notificar."
        )
        return

    alert_id = int(existing_alert["id"])
    status = str(existing_alert["status"])

    if status == "integrator_notified":
        print(f"Alerta {alert_id} já foi notificado para a integradora.")
        return

    if status == "pending_confirmation":
        confirm_maintenance_alert(alert_id)
        status = "confirmed"
        print(f"Alerta {alert_id} confirmado por nova detecção.")

    if status != "confirmed":
        raise RuntimeError(
            f"Estado inesperado do alerta de manutenção {alert_id}: {status}"
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
    print(f"Alerta {alert_id} enviado para a integradora.")


if __name__ == "__main__":
    main()
