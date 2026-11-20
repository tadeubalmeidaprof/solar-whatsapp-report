from datetime import datetime
from zoneinfo import ZoneInfo

from config import env, required_env
from growatt_client import fetch_growatt_payload
from utils import br_number
from whatsapp import send_green_api


REPORT_TIMEZONE = ZoneInfo("America/Bahia")


def build_message_tadeu(payload: dict) -> str:
    today = float(payload.get("energyTodayKwh") or 0)
    month = float(payload.get("energyMonthKwh") or 0)
    date_text = datetime.now(REPORT_TIMEZONE).strftime("%d/%m/%Y")

    return f"""☀️ Tadeu, aqui está seu relatório solar diário! - {date_text}

Geração hoje: {br_number(today, 1)} kWh
Geração no mês: {br_number(month, 1)} kWh
"""


def build_message_pessoa2(payload: dict) -> str:
    today = float(payload.get("energyTodayKwh") or 0)
    month = float(payload.get("energyMonthKwh") or 0)
    date_text = datetime.now(REPORT_TIMEZONE).strftime("%d/%m/%Y")

    return f"""☀️ Rangel, aqui está seu relatório solar diário! - {date_text}

Hoje a usina gerou: {br_number(today, 1)} kWh
Total gerado no mês: {br_number(month, 1)} kWh
"""


def send_daily_report(payload: dict) -> None:
    message_tadeu = build_message_tadeu(payload)
    message_pessoa2 = build_message_pessoa2(payload)

    print("Mensagem para Tadeu:")
    print(message_tadeu)

    send_errors = []

    try:
        send_green_api(
            api_url=env("GREEN_API_URL"),
            id_instance=required_env("GREEN_API_ID"),
            api_token=required_env("GREEN_API_TOKEN"),
            chat_id=required_env("GREEN_API_CHAT_ID"),
            message=message_tadeu,
        )
    except Exception as exc:
        send_errors.append(f"GREEN-API: {exc}")
        print(f"Falha ao enviar para Tadeu pela GREEN-API: {exc}")

    chat_id_2 = env("GREEN_API_CHAT_ID_2")

    if chat_id_2:
        print("Mensagem para Rangel:")
        print(message_pessoa2)

        try:
            send_green_api(
                api_url=env("GREEN_API_URL"),
                id_instance=required_env("GREEN_API_ID"),
                api_token=required_env("GREEN_API_TOKEN"),
                chat_id=chat_id_2,
                message=message_pessoa2,
            )
        except Exception as exc:
            send_errors.append(f"GREEN-API (Rangel): {exc}")
            print(f"Falha ao enviar para Rangel pela GREEN-API: {exc}")
    else:
        print("Segundo número não configurado. Enviado apenas para o número principal.")

    if send_errors:
        raise RuntimeError(
            "Falha em um ou mais envios de WhatsApp: " + " | ".join(send_errors)
        )

    print("Relatório enviado com sucesso.")


def main() -> None:
    payload = fetch_growatt_payload()
    print("Payload Growatt:", payload)
    send_daily_report(payload)


if __name__ == "__main__":
    main()
