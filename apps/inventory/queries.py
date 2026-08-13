"""Freshness, expressed as SQL rather than only as a property.

``freshness.stock_view`` decides what a *card* shows: an item whose confirmed
quantity has expired reads «استعلام موجودی» rather than a number nobody should
trust. The filters did not know that. They compared the stored ``available_sqm``
and ``stock_mode`` directly, so «حداقل ۱۰۰ متر» returned items that, on the very
same page, said they had no current quantity at all.

The two must agree, and the only way to keep them agreeing is to derive both from
one definition. These predicates are that definition's query half; the property
half lives on :class:`~apps.inventory.models.InventoryLot`.
"""

from __future__ import annotations

from django.db.models import Q
from django.utils import timezone

from .models import InventoryLot


def stock_is_fresh_q(prefix: str = "") -> Q:
    """The confirmed quantity is still within its validity window."""
    field = f"{prefix}__" if prefix else ""
    return Q(**{f"{field}stock_expires_at__isnull": False}) & Q(
        **{f"{field}stock_expires_at__gt": timezone.now()}
    )


def effective_stock_mode_q(mode: str, prefix: str = "") -> Q:
    """Items whose *current* stock mode is ``mode``.

    The stored mode is what the seller chose; the effective one is what a viewer
    is actually shown. They differ exactly when a quantity has gone stale, at
    which point the item behaves as inquiry-only until it is reconfirmed.
    """
    field = f"{prefix}__" if prefix else ""
    stored = Q(**{f"{field}stock_mode": mode})
    fresh = stock_is_fresh_q(prefix)

    if mode == InventoryLot.StockMode.INQUIRY:
        # Deliberate inquiry items, plus everything that has expired into being
        # one.
        return stored | (~Q(**{f"{field}stock_mode": InventoryLot.StockMode.INQUIRY}) & ~fresh)
    return stored & fresh


def current_quantity_q(minimum, prefix: str = "") -> Q:
    """Items that can currently be said to have at least ``minimum`` square metres.

    Unlimited stock satisfies any minimum. An exact quantity satisfies it only
    while the confirmation still holds — an expired number is not a smaller
    number, it is no number, and must not answer a quantity question.
    """
    field = f"{prefix}__" if prefix else ""
    return effective_stock_mode_q(InventoryLot.StockMode.UNLIMITED, prefix) | (
        effective_stock_mode_q(InventoryLot.StockMode.EXACT, prefix)
        & Q(**{f"{field}available_sqm__gte": minimum})
    )
