from datetime import datetime
from zoneinfo import ZoneInfo

from config import env, required_env
from growatt_client import fetch_growatt_payload
from utils import br_number
from whatsapp import send_green_api, send_whatsapp_to


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


def main() -> None:
    payload = fetch_growatt_payload()
    print("Payload Growatt:", payload)

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

    phone_2 = env("WHATSAPP_PHONE_2")
    apikey_2 = env("WHATSAPP_APIKEY_2")

    if phone_2 and apikey_2:
        print("Mensagem para segunda pessoa:")
        print(message_pessoa2)

        try:
            send_whatsapp_to(
                phone_2,
                apikey_2,
                message_pessoa2,
            )
        except Exception as exc:
            send_errors.append(f"CallMeBot: {exc}")
            print(f"Falha ao enviar para a segunda pessoa pelo CallMeBot: {exc}")
    else:
        print("Segundo número não configurado. Enviado apenas para o número principal.")

    if send_errors:
        raise RuntimeError(
            "Falha em um ou mais envios de WhatsApp: " + " | ".join(send_errors)
        )

    print("Relatório enviado com sucesso.")


if __name__ == "__main__":
    main()
