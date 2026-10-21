from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone

from apps.core.formatting import format_rial
from apps.inventory.freshness import stock_view
from apps.inventory.models import InventoryLot
from apps.pricing.services import effective_price, resolve_prices_for_viewer


def b2b_price_context(lot: InventoryLot, viewer_business=None) -> dict:
    """Return the audience-safe current B2B price for a marketplace product."""
    prices = resolve_prices_for_viewer(
        lot, "b2b_partner", viewer_business=viewer_business
    )
    price = effective_price(prices, "b2b_partner")
    if price is None or price.amount is None:
        return {
            "has_price": False,
            "amount": None,
            "currency": None,
            "label": "استعلام قیمت",
            "is_special": False,
            "regular_label": "",
            "discount_percent": None,
            "remaining_label": "",
        }
    regular_amount = price.regular_amount if price.is_special else None
    discount_percent = None
    if regular_amount and price.amount:
        raw = (regular_amount - price.amount) / regular_amount * Decimal("100")
        discount_percent = min(99, max(0, int(raw.quantize(Decimal("1"), rounding=ROUND_HALF_UP))))
    remaining_label = ""
    if price.special_until:
        seconds = max(0, int((price.special_until - timezone.now()).total_seconds()))
        days, remainder = divmod(seconds, 86400)
        hours = remainder // 3600
        if days:
            remaining_label = f"{days} روز و {hours} ساعت تا پایان پیشنهاد"
        elif hours:
            remaining_label = f"{hours} ساعت تا پایان پیشنهاد"
        else:
            remaining_label = f"{max(1, seconds // 60)} دقیقه تا پایان پیشنهاد"
    return {
        "has_price": True,
        "amount": price.amount,
        "currency": price.currency,
        "label": format_rial(price.amount),
        "is_special": price.is_special,
        "special_until": price.special_until,
        "regular_label": format_rial(regular_amount) if regular_amount else "",
        "discount_percent": discount_percent,
        "remaining_label": remaining_label,
    }


def marketplace_lot_card(lot: InventoryLot, viewer_business=None) -> dict:
    primary = next(
        (media for media in lot.media.all() if media.kind == "image" and media.is_primary),
        None,
    ) or next((media for media in lot.media.all() if media.kind == "image"), None)
    dimensions = []
    if lot.length_cm:
        dimensions.append(f"{lot.length_cm:g}")
    if lot.width_cm:
        dimensions.append(f"{lot.width_cm:g}")
    return {
        "lot": lot,
        "product": lot.product,
        "supplier": lot.business,
        "price": b2b_price_context(lot, viewer_business),
        "stock": stock_view(lot),
        "primary_media": primary,
        "applications": list(lot.product.applications.all())[:2],
        "size_label": " × ".join(dimensions) + " سانتی‌متر" if dimensions else "ابعاد آزاد",
    }
