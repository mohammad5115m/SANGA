from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

from django.db import transaction
from django.utils import timezone

from apps.businesses.permissions import PRICES_EDIT

from .models import LotPrice, PriceTier

logger = logging.getLogger(__name__)

Audience = Literal["owner_staff", "b2b_partner", "b2c_public", "platform_admin"]


class PricingError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class PriceView:
    """What one audience may be told about one price.

    ``amount`` is ``None`` whenever the number must not be shown — inquiry mode,
    or a fixed price whose confirmation window has lapsed. Callers render
    «استعلام قیمت» in that case and never have a stale figure to accidentally
    display.
    """

    tier_code: str
    amount: Decimal | None
    currency: str
    display_as_inquiry: bool = False
    is_special: bool = False
    special_until: timezone.datetime | None = None
    expired: bool = False


_AUDIENCE_TIERS: dict[Audience, tuple[str, ...]] = {
    "owner_staff": ("b2b", "b2c"),
    "b2b_partner": ("b2b",),
    "b2c_public": ("b2c",),
    "platform_admin": ("b2b", "b2c"),
}


# What to show, in order, once the audience filter has run.
_FALLBACK_ORDER: dict[Audience, tuple[str, ...]] = {
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


def price_view(price: LotPrice) -> PriceView:
    """Turn one stored row into the audience-safe view of it.

    Every read path funnels through here, so expiry and special-sale handling
    cannot be forgotten by an individual caller.
    """
    amount = price.effective_amount()
    is_inquiry_mode = price.mode == LotPrice.Mode.INQUIRY
    return PriceView(
        tier_code=price.tier.code,
        amount=amount,
        currency=price.currency,
        display_as_inquiry=amount is None,
        is_special=price.special_is_live,
        special_until=price.special_until if price.special_is_live else None,
        expired=amount is None and not is_inquiry_mode,
    )


def resolve_visible_prices(lot, audience: Audience, *, can_view_prices: bool = True) -> dict[str, PriceView]:
    """Return only the price tiers this audience is allowed to see.

    Security rule: a disallowed tier must not appear in the result at all, not
    even with a blanked amount. Callers serialize this dict into templates and
    JSON, so absence is the only reliable protection.
    """
    if audience == "owner_staff" and not can_view_prices:
        return {}

    allowed = set(allowed_tiers_for_audience(audience))
    prefetched = getattr(lot, "_prefetched_objects_cache", {})
    if "prices" in prefetched:
        # List pages prefetch tier-filtered prices; calling .filter() here would
        # bypass the cache and fire one query per item.
        candidates = [p for p in lot.prices.all() if p.tier.code in allowed and p.tier.is_active]
    else:
        candidates = list(
            lot.prices.select_related("tier").filter(
                tier__code__in=allowed,
                tier__is_active=True,
            )
        )
    return {price.tier.code: price_view(price) for price in candidates}


def resolve_prices_for_viewer(
    lot,
    audience: Audience,
    *,
    viewer_business=None,
    can_view_prices: bool = True,
) -> dict[str, PriceView]:
    """Kept as the public entry point for viewer-aware price resolution.

    V2 has no per-colleague price overrides, so this is currently a thin pass
    through to :func:`resolve_visible_prices`. It stays because every caller
    already routes through it, and a future viewer-dependent rule should have
    exactly one place to land.
    """
    return resolve_visible_prices(lot, audience, can_view_prices=can_view_prices)


def effective_price(prices: dict[str, PriceView], audience: Audience) -> PriceView | None:
    """Pick the one price to show, or ``None`` for «استعلام قیمت»."""
    for code in _FALLBACK_ORDER[audience]:
        view = prices.get(code)
        if view is not None:
            return view
    return None


def _require_price_edit(membership) -> None:
    if membership is None or not membership.has_capability(PRICES_EDIT):
        raise PricingError("اجازه مدیریت قیمت را ندارید.")


def _quantize_amount(value) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PricingError("مبلغ واردشده معتبر نیست.") from exc


@transaction.atomic
def set_lot_price(
    *,
    lot,
    tier_code: str,
    amount: Decimal | None,
    currency: str = "IRR",
    mode: str | None = None,
    valid_for_days: int | None = None,
    special_amount: Decimal | None = None,
    special_until=None,
) -> LotPrice:
    """Set one audience's price for one item.

    Passing ``amount=None`` (or ``mode='inquiry'``) stores an inquiry-only price
    rather than a zero, so «استعلام قیمت» and «رایگان» stay distinguishable.
    """
    ensure_default_tiers()
    if tier_code not in {"b2b", "b2c"}:
        raise ValueError("سطح قیمت نامعتبر است.")
    tier = PriceTier.objects.get(code=tier_code)

    if mode is None:
        mode = LotPrice.Mode.INQUIRY if amount is None else LotPrice.Mode.FIXED
    if mode not in set(LotPrice.Mode.values):
        raise ValueError("نوع قیمت نامعتبر است.")

    if mode == LotPrice.Mode.INQUIRY:
        amount = None
        special_amount = None
        special_until = None
    else:
        if amount is None:
            raise ValueError("مبلغ قیمت الزامی است.")
        amount = _quantize_amount(amount)
        if amount <= 0:
            raise ValueError("مبلغ قیمت باید بیشتر از صفر باشد.")
        if special_amount is not None:
            special_amount = _quantize_amount(special_amount)
            if special_amount <= 0:
                raise ValueError("مبلغ فروش ویژه باید بیشتر از صفر باشد.")
        if (special_amount is None) != (special_until is None):
            raise ValueError("مبلغ و زمان پایان فروش ویژه باید با هم وارد شوند.")
        if special_amount is not None:
            if special_amount >= amount:
                raise ValueError("قیمت فروش ویژه باید کمتر از قیمت عادی باشد.")
            if special_until <= timezone.now():
                raise ValueError("زمان پایان فروش ویژه باید در آینده باشد.")

    if valid_for_days is not None and not 1 <= int(valid_for_days) <= 365:
        raise ValueError("اعتبار قیمت باید بین ۱ تا ۳۶۵ روز باشد.")

    defaults = {
        "mode": mode,
        "amount": amount,
        "currency": currency or "IRR",
        "special_amount": special_amount,
        "special_until": special_until,
        # Setting a price is itself a confirmation of it.
        "price_confirmed_at": timezone.now(),
    }
    if valid_for_days is not None:
        defaults["price_valid_for_days"] = int(valid_for_days)

    price, _created = LotPrice.objects.update_or_create(lot=lot, tier=tier, defaults=defaults)
    return price


@transaction.atomic
def confirm_lot_price(*, lot, tier_code: str, membership) -> LotPrice | None:
    """Restart the validity window without changing the number.

    The common case after an expiry: the seller looks at the price, decides it
    is still right, and says so.
    """
    _require_price_edit(membership)
    if membership.business_id != lot.business_id:
        raise PricingError("این محصول متعلق به کسب‌وکار شما نیست.")

    price = lot.prices.select_related("tier").filter(tier__code=tier_code).first()
    if price is None:
        return None
    price.price_confirmed_at = timezone.now()
    price.save(update_fields=["price_confirmed_at", "updated_at"])
    return price
