import calendar
import os
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from database import fetch_generation_for_month, to_decimal
from savings_calculator import calculate_savings_with_fio_b
from send_daily_report import br_number, env, send_whatsapp_to


REPORT_TIMEZONE = ZoneInfo("America/Bahia")
CONNECTION_TYPE = "monofasico"


MONTH_NAMES_PT = {
    1: "janeiro",
    2: "fevereiro",
    3: "março",
    4: "abril",
    5: "maio",
    6: "junho",
    7: "julho",
    8: "agosto",
    9: "setembro",
    10: "outubro",
    11: "novembro",
    12: "dezembro",
}


def month_info_from_date(reference_date: date) -> tuple[str, str, int]:
    year_month = reference_date.strftime("%Y-%m")
    month_label = f"{MONTH_NAMES_PT[reference_date.month]} de {reference_date.year}"
    days_in_month = calendar.monthrange(reference_date.year, reference_date.month)[1]

    return year_month, month_label, days_in_month


def previous_month(reference_date: date) -> tuple[str, str, int]:
    first_day_current_month = reference_date.replace(day=1)
    last_day_previous_month = first_day_current_month - timedelta(days=1)

    return month_info_from_date(last_day_previous_month)


def get_report_month(reference_date: date) -> tuple[str, str, int]:
    forced_year_month = os.getenv("REPORT_YEAR_MONTH", "").strip()

    if not forced_year_month:
        return previous_month(reference_date)

    try:
        forced_date = datetime.strptime(forced_year_month, "%Y-%m").date()
    except ValueError as exc:
        raise RuntimeError(
            "REPORT_YEAR_MONTH inválido. Use o formato YYYY-MM, por exemplo 2026-07."
        ) from exc

    return month_info_from_date(forced_date)


def format_brl(value: Decimal) -> str:
    rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    text = f"{rounded:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {text}"


def get_tariff() -> Decimal:
    value = env("ENERGY_TARIFF", "0", required=True)
    return to_decimal(value)


def get_average_consumption(generation_kwh: Decimal) -> Decimal:
    configured = os.getenv("AVERAGE_CONSUMPTION_KWH", "").strip()

    if configured:
        return to_decimal(configured)

    # Fallback seguro para manter o relatório funcionando quando o consumo médio
    # ainda não foi cadastrado. Considera que toda a geração do mês foi compensável
    # e soma o mínimo monofásico de 30 kWh.
    return generation_kwh + Decimal("30")


def get_fio_b_inputs() -> tuple[Decimal, Decimal]:
    tusd_fio_b_kwh = to_decimal(env("TUSD_FIO_B_KWH", required=True))
    fio_b_percentage = to_decimal(env("FIO_B_PERCENTAGE", required=True))

    return tusd_fio_b_kwh, fio_b_percentage


def build_monthly_message(
    customer_name: str,
    month_label: str,
    generation_kwh: Decimal,
    average_daily_kwh: Decimal,
    tariff: Decimal,
    savings: Decimal,
) -> str:
    return f"""☀️ {customer_name}, aqui está seu relatório solar mensal!

Resumo de {month_label}:

⚡ Geração total: {br_number(generation_kwh, 1)} kWh
📊 Média diária: {br_number(average_daily_kwh, 1)} kWh/dia
💰 Economia estimada: {format_brl(savings)}

Tarifa considerada: {format_brl(tariff)} por kWh.

Valor estimado com base na geração registrada no monitoramento solar.
"""


def should_send_to_second_person() -> bool:
    return bool(os.getenv("WHATSAPP_PHONE_2", "").strip() and os.getenv("WHATSAPP_APIKEY_2", "").strip())


def main():
    today = datetime.now(REPORT_TIMEZONE).date()
    year_month, month_label, days_in_month = get_report_month(today)

    station_id = os.getenv("GROWATT_PLANT_ID", "").strip()
    result = fetch_generation_for_month(year_month=year_month, station_id=station_id or None)

    if not result:
        raise RuntimeError(
            f"Nenhum registro encontrado no Supabase para o mês {year_month}. "
            "Verifique se o snapshot mensal está rodando corretamente."
        )

    found_station_id, generation_kwh = result
    tariff = get_tariff()
    average_consumption_kwh = get_average_consumption(generation_kwh)
    tusd_fio_b_kwh, fio_b_percentage = get_fio_b_inputs()
    savings_data = calculate_savings_with_fio_b(
        generation_month_kwh=generation_kwh,
        average_consumption_month_kwh=average_consumption_kwh,
        final_tariff_kwh=tariff,
        tusd_fio_b_kwh=tusd_fio_b_kwh,
        fio_b_percentage=fio_b_percentage,
        connection_type=CONNECTION_TYPE,
    )
    savings = savings_data["estimated_savings"]
    average_daily_kwh = generation_kwh / Decimal(days_in_month)

    print(
        "Dados do relatório mensal:",
        {
            "station_id": found_station_id,
            "year_month": year_month,
            "generation_kwh": str(generation_kwh),
            "average_consumption_kwh": str(average_consumption_kwh),
            "tariff": str(tariff),
            "tusd_fio_b_kwh": str(tusd_fio_b_kwh),
            "fio_b_percentage": str(fio_b_percentage),
            "savings": str(savings),
            "average_daily_kwh": str(average_daily_kwh),
        },
    )

    message_tadeu = build_monthly_message(
        customer_name="Tadeu",
        month_label=month_label,
        generation_kwh=generation_kwh,
        average_daily_kwh=average_daily_kwh,
        tariff=tariff,
        savings=savings,
    )

    print("Mensagem mensal para Tadeu:")
    print(message_tadeu)

    send_whatsapp_to(
        env("WHATSAPP_PHONE", required=True),
        env("WHATSAPP_APIKEY", required=True),
        message_tadeu,
    )

    if should_send_to_second_person():
        message_rangel = build_monthly_message(
            customer_name="Rangel",
            month_label=month_label,
            generation_kwh=generation_kwh,
            average_daily_kwh=average_daily_kwh,
            tariff=tariff,
            savings=savings,
        )

        print("Mensagem mensal para Rangel:")
        print(message_rangel)

        send_whatsapp_to(
            env("WHATSAPP_PHONE_2"),
            env("WHATSAPP_APIKEY_2"),
            message_rangel,
        )
    else:
        print("Segundo número não configurado. Enviado apenas para o número principal.")

    print("Relatório mensal enviado com sucesso.")


if __name__ == "__main__":
    main()
