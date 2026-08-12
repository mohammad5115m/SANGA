"""Stock freshness, derived at read time.

SANGA is not the authoritative warehouse system. It records the last thing the
seller confirmed and is honest about how old that is. Nothing in this module
writes to the database: freshness is a function of ``stock_confirmed_at`` and
``stock_valid_for_days``, so an hourly job mutating rows to express it would be
both redundant and a source of write amplification.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from django.utils import timezone

from .models import InventoryLot


class StockDisplay(StrEnum):
    """What a viewer should be told about the quantity."""

    EXACT = "exact"
    UNLIMITED = "unlimited"
    INQUIRY = "inquiry"


@dataclass(frozen=True)
class StockView:
    """Audience-safe description of an item's stock.

    ``quantity_sqm`` is populated only for :attr:`StockDisplay.EXACT`. Once the
    confirmation window lapses the number is withheld rather than shown with a
    caveat, because a stale figure that looks authoritative is worse than no
    figure at all.
    """

    display: StockDisplay
    label: str
    quantity_sqm: object | None
    confirmed_at: timezone.datetime | None
    human_confirmed: str
    expires_at: timezone.datetime | None
    needs_confirmation: bool

    @property
    def is_inquiry(self) -> bool:
        return self.display == StockDisplay.INQUIRY


def humanize_confirmed(confirmed_at: timezone.datetime | None) -> str:
    if confirmed_at is None:
        return "هنوز تأیید نشده"
    now = timezone.now()
    local = timezone.localtime(confirmed_at)
    delta = now - confirmed_at
    if delta < timedelta(hours=24) and local.date() == timezone.localdate():
        return f"امروز، {local.strftime('%H:%M')}"
    if delta < timedelta(hours=48):
        return f"دیروز، {local.strftime('%H:%M')}"
    return local.strftime("%Y/%m/%d، %H:%M")


def stock_view(lot: InventoryLot) -> StockView:
    """Resolve what to show for this item's stock, right now."""
    confirmed_at = lot.stock_confirmed_at
    human = humanize_confirmed(confirmed_at)
    effective = lot.effective_stock_mode
    expires_at = lot.stock_expires_at

    if effective == InventoryLot.StockMode.UNLIMITED:
        return StockView(
            display=StockDisplay.UNLIMITED,
            label="موجودی نامحدود",
            quantity_sqm=None,
            confirmed_at=confirmed_at,
            human_confirmed=human,
            expires_at=expires_at,
            needs_confirmation=False,
        )

    if effective == InventoryLot.StockMode.EXACT:
        return StockView(
            display=StockDisplay.EXACT,
            label=f"{lot.available_sqm:,.0f} متر مربع",
            quantity_sqm=lot.available_sqm,
            confirmed_at=confirmed_at,
            human_confirmed=human,
            expires_at=expires_at,
            needs_confirmation=False,
        )

    # Either the seller chose inquiry mode, or a quantity went stale. The seller
    # is asked to reconfirm only in the second case: a deliberate inquiry item
    # has nothing to refresh.
    return StockView(
        display=StockDisplay.INQUIRY,
        label="استعلام موجودی",
        quantity_sqm=None,
        confirmed_at=confirmed_at,
        human_confirmed=human,
        expires_at=expires_at,
        needs_confirmation=lot.stock_mode != InventoryLot.StockMode.INQUIRY,
    )


def needs_stock_confirmation(lot: InventoryLot) -> bool:
    """True when the seller has a quantity that has stopped being trustworthy."""
    if lot.stock_mode == InventoryLot.StockMode.INQUIRY:
        return False
    return not lot.is_stock_fresh
