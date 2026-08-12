from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

from django.db import IntegrityError, transaction

from apps.businesses.permissions import PRICES_EDIT

from .models import ContactPrice, LotPrice, PriceTier

logger = logging.getLogger(__name__)

Audience = Literal["owner_staff", "b2b_partner", "b2c_public", "platform_admin"]

# Pseudo-tier code for a partner-specific override. It is deliberately not a
# ``PriceTier`` row: an override belongs to one contact, not to an audience.
CONTACT_TIER_CODE = "contact"


class PricingError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


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


# Audiences that may ever be shown a partner-specific override. B2C and
# anonymous viewers are excluded here, by construction, rather than relying on
# every caller remembering to opt out.
_OVERRIDE_AUDIENCES: frozenset[str] = frozenset({"b2b_partner"})

# What to show, in order, once the audience filter has run. A partner-specific
# override beats the partner's own tier; nothing left means «استعلام بگیرید».
_FALLBACK_ORDER: dict[Audience, tuple[str, ...]] = {
    "owner_staff": ("b2b", "b2c"),
    "b2b_partner": (CONTACT_TIER_CODE, "b2b"),
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

    allowed = set(allowed_tiers_for_audience(audience))
    prefetched = getattr(lot, "_prefetched_objects_cache", {})
    if "prices" in prefetched:
        # Use the prefetch cache (list pages prefetch tier-filtered prices);
        # calling .filter() here would bypass it and fire one query per lot.
        candidates = [p for p in lot.prices.all() if p.tier.code in allowed and p.tier.is_active]
    else:
        candidates = list(
            lot.prices.select_related("tier").filter(
                tier__code__in=allowed,
                tier__is_active=True,
            )
        )
    result: dict[str, PriceView] = {}
    for price in candidates:
        result[price.tier.code] = PriceView(
            tier_code=price.tier.code,
            amount=None if price.unit == LotPrice.Unit.INQUIRY_ONLY else price.amount,
            currency=price.currency,
            unit=price.unit,
            display_as_inquiry=price.unit == LotPrice.Unit.INQUIRY_ONLY,
        )
    return result


def _contact_price_row(lot, viewer_business) -> ContactPrice | None:
    """The override row that applies to ``viewer_business`` for ``lot``, if any.

    Both branches re-check the two facts that make an override legitimate — the
    contact belongs to the lot's owner, and it is linked to this viewer — so a
    stale prefetch or bad legacy row still cannot leak someone else's price.
    """
    prefetched = getattr(lot, "_prefetched_objects_cache", {})
    if "contact_prices" in prefetched:
        # List pages prefetch the viewer's overrides; calling .filter() here
        # would bypass the cache and fire one query per lot.
        rows = list(lot.contact_prices.all())
    else:
        rows = list(
            lot.contact_prices.select_related("contact").filter(
                contact__linked_business=viewer_business,
                contact__is_active=True,
            )
        )
    for row in rows:
        contact = row.contact
        if (
            contact.linked_business_id == viewer_business.id
            and contact.business_id == lot.business_id
            and contact.is_active
        ):
            return row
    return None


def resolve_contact_price(lot, viewer_business, *, audience: Audience) -> PriceView | None:
    """The partner-specific override for this viewer, or ``None``.

    Returns ``None`` for every audience except ``b2b_partner`` and for anonymous
    viewers (``viewer_business is None``), so the public catalog can never
    surface an override even if a caller asks for one.
    """
    if audience not in _OVERRIDE_AUDIENCES or viewer_business is None:
        return None

    row = _contact_price_row(lot, viewer_business)
    if row is None:
        return None
    return PriceView(
        tier_code=CONTACT_TIER_CODE,
        amount=None if row.unit == LotPrice.Unit.INQUIRY_ONLY else row.amount,
        currency=row.currency,
        unit=row.unit,
        display_as_inquiry=row.unit == LotPrice.Unit.INQUIRY_ONLY,
    )


def resolve_prices_for_viewer(
    lot,
    audience: Audience,
    *,
    viewer_business=None,
    can_view_prices: bool = True,
) -> dict[str, PriceView]:
    """``resolve_visible_prices`` plus any partner-specific override.

    The override is added on top of the audience-filtered tiers rather than
    instead of them, so it goes through exactly the same visibility rules.
    """
    visible = resolve_visible_prices(lot, audience, can_view_prices=can_view_prices)
    override = resolve_contact_price(lot, viewer_business, audience=audience)
    if override is not None:
        visible[CONTACT_TIER_CODE] = override
    return visible


def effective_price(prices: dict[str, PriceView], audience: Audience) -> PriceView | None:
    """Pick the one price to show: partner-specific override → the audience's own
    tier → ``None``, which the UI renders as «استعلام بگیرید».
    """
    for code in _FALLBACK_ORDER[audience]:
        view = prices.get(code)
        if view is not None:
            return view
    return None


def _require_price_edit(membership) -> None:
    # prices.edit is the existing capability for managing prices; a
    # partner-specific price is still a price.
    if membership is None or not membership.has_capability(PRICES_EDIT):
        raise PricingError("اجازه مدیریت قیمت را ندارید.")


def _quantize_amount(value) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PricingError("مبلغ واردشده معتبر نیست.") from exc


def _check_ownership(*, lot, contact, membership) -> None:
    if membership.business_id != lot.business_id:
        raise PricingError("این محموله متعلق به کسب‌وکار شما نیست.")
    if contact.business_id != lot.business_id:
        raise PricingError("این مخاطب متعلق به کسب‌وکار شما نیست.")


@transaction.atomic
def set_contact_price(
    *,
    lot,
    contact,
    membership,
    amount,
    currency: str = "IRR",
    unit: str = LotPrice.Unit.PER_SQM,
) -> ContactPrice:
    """Create or update the price this one contact is quoted for ``lot``."""
    _require_price_edit(membership)
    _check_ownership(lot=lot, contact=contact, membership=membership)

    if unit not in set(LotPrice.Unit.values):
        raise PricingError("واحد قیمت نامعتبر است.")
    if unit == LotPrice.Unit.INQUIRY_ONLY:
        amount = Decimal("0")
    else:
        amount = _quantize_amount(amount)
        # A zero override would read as «رایگان»; sellers who want no number
        # should pick «فقط استعلام» instead.
        if amount <= 0:
            raise PricingError("مبلغ قیمت اختصاصی باید بزرگ‌تر از صفر باشد.")

    try:
        price, _created = ContactPrice.objects.update_or_create(
            contact=contact,
            lot=lot,
            defaults={
                "amount": amount,
                "currency": (currency or "IRR").strip() or "IRR",
                "unit": unit,
                "created_by": membership.user,
            },
        )
    except IntegrityError as exc:
        raise PricingError("ثبت قیمت اختصاصی ممکن نشد؛ دوباره تلاش کنید.") from exc

    logger.info(
        "Contact price set lot=%s contact=%s business=%s amount=%s",
        lot.id,
        contact.id,
        lot.business_id,
        amount,
    )
    return price


@transaction.atomic
def remove_contact_price(*, lot, contact, membership) -> None:
    """Drop the override so this contact falls back to the normal B2B tier."""
    _require_price_edit(membership)
    _check_ownership(lot=lot, contact=contact, membership=membership)

    deleted, _ = ContactPrice.objects.filter(lot=lot, contact=contact).delete()
    if deleted:
        logger.info(
            "Contact price removed lot=%s contact=%s business=%s",
            lot.id,
            contact.id,
            lot.business_id,
        )


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
