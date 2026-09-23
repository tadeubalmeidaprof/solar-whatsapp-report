from datetime import datetime
from zoneinfo import ZoneInfo

from config import env
from growatt_client import fetch_growatt_payload
from utils import br_number
from whatsapp import send_whatsapp_to


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

    send_whatsapp_to(
        env("WHATSAPP_PHONE", required=True),
        env("WHATSAPP_APIKEY", required=True),
        message_tadeu,
    )

    phone_2 = env("WHATSAPP_PHONE_2")
    apikey_2 = env("WHATSAPP_APIKEY_2")

    if phone_2 and apikey_2:
        print("Mensagem para segunda pessoa:")
        print(message_pessoa2)

        send_whatsapp_to(
            phone_2,
            apikey_2,
            message_pessoa2,
        )
    else:
        print("Segundo número não configurado. Enviado apenas para o número principal.")

    print("Relatório enviado com sucesso.")


if __name__ == "__main__":
    main()
