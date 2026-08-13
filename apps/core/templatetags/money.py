"""Money formatting.

``humanize.intcomma`` returns the number *ungrouped* under the ``fa`` locale,
because Django's Persian locale data defines no thousand separator. Rials run to
nine or ten digits, and "60000000" is unreadable at a glance — which for a
trading application is not a cosmetic problem, it is how somebody mis-reads a
price by a factor of ten.

So money gets its own filter rather than relying on locale data to do something
it does not do.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter(name="rial")
def rial(value) -> str:
    """Group a monetary amount in threes, dropping trailing zero decimals.

    ``60000000.00`` becomes ``60,000,000``. Rial amounts are whole numbers in
    practice, and a trailing ``٫۰۰`` on every price is noise.
    """
    if value in (None, ""):
        return "—"
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return str(value)

    negative = amount < 0
    amount = abs(amount)
    whole = int(amount)
    fraction = amount - whole

    text = f"{whole:,}"
    if fraction:
        text = f"{text}.{str(fraction).split('.')[1].rstrip('0')}"
    return f"-{text}" if negative else text


@register.filter(name="sqm")
def sqm(value) -> str:
    """Trim the stored 3-decimal quantity for display (100.000 → 100)."""
    if value in (None, ""):
        return "—"
    try:
        return format(Decimal(str(value)).normalize(), "f")
    except (InvalidOperation, TypeError, ValueError):
        return str(value)
