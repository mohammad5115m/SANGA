from __future__ import annotations

from apps.core.formatting import format_rial
from apps.inventory.freshness import stock_view
from apps.inventory.models import InventoryLot
from apps.pricing.services import effective_price, resolve_prices_for_viewer


class MarketplaceError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def b2b_price_context(lot: InventoryLot, viewer_business=None) -> dict:
    """Colleague-facing price payload: B2B tier only.

    Returns a flat dict with no tier keys, so a template cannot walk it to find
    the B2C number, and an expired or inquiry-mode price arrives already
    reduced to «استعلام قیمت» with no amount attached.
    """
    prices = resolve_prices_for_viewer(lot, "b2b_partner", viewer_business=viewer_business)
    price = effective_price(prices, "b2b_partner")
    if price is None or price.amount is None:
        return {
            "has_price": False,
            "amount": None,
            "currency": None,
            "label": "استعلام قیمت",
            "is_special": False,
        }
    return {
        "has_price": True,
        "amount": price.amount,
        "currency": price.currency,
        "label": format_rial(price.amount),
        "is_special": price.is_special,
        "special_until": price.special_until,
    }


def marketplace_lot_card(lot: InventoryLot, viewer_business=None) -> dict:
    primary = next((m for m in lot.media.all() if m.is_primary), None) or next(iter(lot.media.all()), None)
    return {
        "lot": lot,
        "product": lot.product,
        "supplier": lot.business,
        "price": b2b_price_context(lot, viewer_business),
        "stock": stock_view(lot),
        "primary_media": primary,
    }
