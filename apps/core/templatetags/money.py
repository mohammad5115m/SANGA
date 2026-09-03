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

from django import template

from apps.core.formatting import format_decimal

register = template.Library()


@register.filter(name="rial")
def rial(value) -> str:
    """Group a monetary amount in threes, dropping trailing zero decimals.

    ``60000000.00`` becomes ``60,000,000``. Rial amounts are whole numbers in
    practice, and a trailing ``٫۰۰`` on every price is noise.
    """
    return format_decimal(value, grouped=True)


@register.filter(name="sqm")
def sqm(value) -> str:
    """Trim the stored 3-decimal quantity for display (100.000 → 100)."""
    return format_decimal(value)
