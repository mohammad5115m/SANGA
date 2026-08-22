"""Consistent, domain-level formatting shared by services and templates."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


def format_decimal(value, *, grouped: bool = False) -> str:
    if value in (None, ""):
        return "—"
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return str(value)
    whole, dot, fraction = format(amount, "f").partition(".")
    fraction = fraction.rstrip("0")
    text = f"{whole}.{fraction}" if dot and fraction else whole
    if not grouped:
        return text
    whole, dot, fraction = text.partition(".")
    grouped_whole = f"{int(whole):,}"
    return f"{grouped_whole}.{fraction}" if dot else grouped_whole


def format_rial(value) -> str:
    return f"{format_decimal(value, grouped=True)} ریال"
