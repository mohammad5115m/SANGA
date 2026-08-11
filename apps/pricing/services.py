from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from django.db import transaction
from django.db.models import QuerySet

from .models import LotPrice, PriceTier

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


def ensure_default_tiers() -> None:
    PriceTier.objects.get_or_create(code="b2b", defaults={"name": "قیمت همکار", "sort_order": 1})
    PriceTier.objects.get_or_create(code="b2c", defaults={"name": "قیمت مشتری", "sort_order": 2})


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


@transaction.atomic
def set_lot_price(
    *,
    lot,
    tier_code: str,
    amount: Decimal | None,
    currency: str = "IRR",
    unit: str = LotPrice.Unit.PER_SQM,
) -> LotPrice:
    ensure_default_tiers()
    if tier_code not in {"b2b", "b2c"}:
        raise ValueError("سطح قیمت نامعتبر است.")
    tier = PriceTier.objects.get(code=tier_code)
    if unit == LotPrice.Unit.INQUIRY_ONLY:
        amount = Decimal("0")
    if amount is None:
        raise ValueError("مبلغ قیمت الزامی است.")
    if amount < 0:
        raise ValueError("مبلغ قیمت نمی‌تواند منفی باشد.")

    price, _created = LotPrice.objects.update_or_create(
        lot=lot,
        tier=tier,
        defaults={
            "amount": amount,
            "currency": currency or "IRR",
            "unit": unit,
        },
    )
    return price


@transaction.atomic
def set_lot_prices(
    *,
    lot,
    b2b_amount: Decimal | None,
    b2c_amount: Decimal | None,
    currency: str = "IRR",
    unit: str = LotPrice.Unit.PER_SQM,
) -> None:
    if b2b_amount is not None:
        set_lot_price(lot=lot, tier_code="b2b", amount=b2b_amount, currency=currency, unit=unit)
    if b2c_amount is not None:
        set_lot_price(lot=lot, tier_code="b2c", amount=b2c_amount, currency=currency, unit=unit)
