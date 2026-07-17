from decimal import Decimal, ROUND_HALF_UP


def to_decimal(value) -> Decimal:
    if value is None:
        return Decimal("0")

    if isinstance(value, Decimal):
        return value

    text = str(value).strip().replace(",", ".")

    if not text:
        return Decimal("0")

    return Decimal(text)


def round_money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
