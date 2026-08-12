from __future__ import annotations

from django.db import transaction

from apps.businesses.models import Business
from apps.inventory.models import InventoryLot
from apps.pricing.services import CONTACT_TIER_CODE, effective_price, resolve_prices_for_viewer

from .models import SavedSearch


class MarketplaceError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def b2b_price_context(lot: InventoryLot, viewer_business=None) -> dict:
    """Partner marketplace price payload: B2B only.

    When ``viewer_business`` is given and the lot's owner has set a
    partner-specific price for the contact linked to that business, the override
    is shown instead of the plain B2B tier. Without a viewer there is no
    override — the payload degrades to the tier price.
    """
    prices = resolve_prices_for_viewer(lot, "b2b_partner", viewer_business=viewer_business)
    price = effective_price(prices, "b2b_partner")
    if price is None or price.display_as_inquiry or price.amount is None:
        return {
            "has_price": False,
            "amount": None,
            "currency": None,
            "unit": None,
            "label": "استعلام بگیرید",
            "is_partner_price": False,
        }
    return {
        "has_price": True,
        "amount": price.amount,
        "currency": price.currency,
        "unit": price.unit,
        "label": f"{price.amount:,.0f} {price.currency}",
        "is_partner_price": price.tier_code == CONTACT_TIER_CODE,
    }


@transaction.atomic
def save_search(
    *,
    business: Business,
    user,
    name: str,
    query: dict,
    notify_enabled: bool = True,
) -> SavedSearch:
    name = (name or "").strip()
    if len(name) < 2:
        raise MarketplaceError("نام جستجو خیلی کوتاه است.")
    return SavedSearch.objects.create(
        business=business,
        user=user,
        name=name,
        query=query or {},
        notify_enabled=notify_enabled,
    )


def marketplace_lot_card(lot: InventoryLot, viewer_business=None) -> dict:
    primary = next((m for m in lot.media.all() if m.is_primary), None) or next(iter(lot.media.all()), None)
    return {
        "lot": lot,
        "product": lot.product,
        "supplier": lot.business,
        "price": b2b_price_context(lot, viewer_business),
        "primary_media": primary,
    }
