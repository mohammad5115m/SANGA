from __future__ import annotations

from apps.inventory.models import InventoryLot
from apps.pricing.services import resolve_visible_prices


def b2b_price_context(lot: InventoryLot) -> dict:
    """Partner marketplace price payload: B2B only."""
    prices = resolve_visible_prices(lot, "b2b_partner")
    b2b = prices.get("b2b")
    if b2b is None or b2b.display_as_inquiry or b2b.amount is None:
        return {
            "has_price": False,
            "amount": None,
            "currency": None,
            "unit": None,
            "label": "استعلام بگیرید",
        }
    return {
        "has_price": True,
        "amount": b2b.amount,
        "currency": b2b.currency,
        "unit": b2b.unit,
        "label": f"{b2b.amount:,.0f} {b2b.currency}",
    }


def marketplace_lot_card(lot: InventoryLot) -> dict:
    primary = next((m for m in lot.media.all() if m.is_primary), None) or next(iter(lot.media.all()), None)
    return {
        "lot": lot,
        "product": lot.product,
        "supplier": lot.business,
        "price": b2b_price_context(lot),
        "primary_media": primary,
    }
