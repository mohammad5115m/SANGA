from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum

from django.utils import timezone

from apps.businesses.models import Business

from .models import InventoryLot


class FreshnessLevel(str, Enum):
    FRESH = "fresh"
    NEEDS_CONFIRMATION = "needs_confirmation"
    STALE = "stale"
    HIDDEN = "hidden"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FreshnessPolicy:
    confirm_after_days: int = 7
    stale_after_days: int = 14
    hide_after_days: int = 21


@dataclass(frozen=True)
class FreshnessInfo:
    level: FreshnessLevel
    label: str
    confirmed_at: timezone.datetime | None
    human_confirmed: str


def policy_for_business(business: Business) -> FreshnessPolicy:
    settings = business.settings or {}
    return FreshnessPolicy(
        confirm_after_days=int(settings.get("freshness_confirm_days", 7)),
        stale_after_days=int(settings.get("freshness_stale_days", 14)),
        hide_after_days=int(settings.get("freshness_hide_days", 21)),
    )


def _humanize_confirmed(confirmed_at: timezone.datetime | None) -> str:
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


def evaluate_freshness(lot: InventoryLot, *, policy: FreshnessPolicy | None = None) -> FreshnessInfo:
    policy = policy or policy_for_business(lot.business)
    confirmed_at = lot.inventory_confirmed_at
    human = _humanize_confirmed(confirmed_at)

    if lot.status == InventoryLot.Status.HIDDEN:
        return FreshnessInfo(FreshnessLevel.HIDDEN, "مخفی", confirmed_at, human)
    if confirmed_at is None:
        return FreshnessInfo(FreshnessLevel.UNKNOWN, "بدون تأیید", confirmed_at, human)

    age = timezone.now() - confirmed_at
    if age <= timedelta(days=policy.confirm_after_days):
        return FreshnessInfo(FreshnessLevel.FRESH, "تازه", confirmed_at, human)
    if age <= timedelta(days=policy.stale_after_days):
        return FreshnessInfo(FreshnessLevel.NEEDS_CONFIRMATION, "نیاز به تأیید", confirmed_at, human)
    if age <= timedelta(days=policy.hide_after_days):
        return FreshnessInfo(FreshnessLevel.STALE, "کهنه", confirmed_at, human)
    return FreshnessInfo(FreshnessLevel.HIDDEN, "مخفی‌شونده", confirmed_at, human)


def apply_freshness_transition(lot: InventoryLot, *, policy: FreshnessPolicy | None = None) -> InventoryLot:
    """Update lot status based on confirmation age. Does not touch sold/reserved lots."""
    if lot.status in {
        InventoryLot.Status.SOLD,
        InventoryLot.Status.RESERVED,
        InventoryLot.Status.RESERVATION_PENDING,
        InventoryLot.Status.DRAFT,
        InventoryLot.Status.EXPIRED,
    }:
        return lot

    info = evaluate_freshness(lot, policy=policy)
    new_status = lot.status
    if info.level == FreshnessLevel.NEEDS_CONFIRMATION:
        new_status = InventoryLot.Status.NEEDS_CONFIRMATION
    elif info.level == FreshnessLevel.STALE:
        new_status = InventoryLot.Status.NEEDS_CONFIRMATION
    elif info.level == FreshnessLevel.HIDDEN:
        new_status = InventoryLot.Status.HIDDEN
    elif info.level == FreshnessLevel.FRESH and lot.status == InventoryLot.Status.NEEDS_CONFIRMATION:
        new_status = InventoryLot.Status.AVAILABLE

    if new_status != lot.status:
        lot.status = new_status
        lot.save(update_fields=["status", "updated_at"])
    return lot
