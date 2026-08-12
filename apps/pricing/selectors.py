"""Read helpers for pricing.

The per-contact override selectors that used to live here went away with
``ContactPrice`` in V2. Price questions now have exactly two answers — the B2B
tier and the B2C tier — so there is nothing viewer-dependent left to look up.
"""

from __future__ import annotations

from django.db.models import QuerySet

from .models import LotPrice


def prices_for_item(lot) -> QuerySet[LotPrice]:
    return LotPrice.objects.filter(lot=lot).select_related("tier").order_by("tier__sort_order")


def stale_prices_for_business(business) -> QuerySet[LotPrice]:
    """Fixed prices whose validity window has lapsed, for the seller's to-do list."""
    return (
        LotPrice.objects.filter(lot__business=business, lot__deleted_at__isnull=True)
        .filter(LotPrice.needs_confirmation_q())
        .select_related("tier", "lot", "lot__product")
        .order_by("price_expires_at")
    )
