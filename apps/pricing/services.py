from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from django.db.models import QuerySet

from .models import LotPrice

Audience = Literal["owner_staff", "b2b_partner", "b2c_public", "platform_admin"]


@dataclass(frozen=True)
class PriceView:
    tier_code: str
    amount: Decimal | None
    currency: str
    unit: str
    display_as_inquiry: bool = False


_AUDIENCE_TIERS: dict[Audience, tuple[str, ...]] = {
    "owner_staff": ("b2b", "b2c"),
    "b2b_partner": ("b2b",),
    "b2c_public": ("b2c",),
    "platform_admin": ("b2b", "b2c"),
}


def allowed_tiers_for_audience(audience: Audience) -> tuple[str, ...]:
    return _AUDIENCE_TIERS[audience]


def resolve_visible_prices(lot, audience: Audience, *, can_view_prices: bool = True) -> dict[str, PriceView]:
    """
    Return only price tiers visible to the audience.

    Security rule: disallowed tiers must not appear in the result at all.
    """
    if audience == "owner_staff" and not can_view_prices:
        return {}

    allowed = allowed_tiers_for_audience(audience)
    prices: QuerySet[LotPrice] = lot.prices.select_related("tier").filter(
        tier__code__in=allowed,
        tier__is_active=True,
    )
    result: dict[str, PriceView] = {}
    for price in prices:
        result[price.tier.code] = PriceView(
            tier_code=price.tier.code,
            amount=None if price.unit == LotPrice.Unit.INQUIRY_ONLY else price.amount,
            currency=price.currency,
            unit=price.unit,
            display_as_inquiry=price.unit == LotPrice.Unit.INQUIRY_ONLY,
        )
    return result
