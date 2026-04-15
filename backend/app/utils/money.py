from decimal import Decimal, ROUND_HALF_UP


def to_vnd_minor(amount: float | int | str | Decimal) -> int:
    value = Decimal(str(amount))
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def from_vnd_minor(amount_minor: int) -> float:
    return float(Decimal(amount_minor))
