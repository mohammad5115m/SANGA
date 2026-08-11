from __future__ import annotations

import logging

from celery import shared_task

from apps.businesses.models import Business

from .freshness import apply_freshness_transition, policy_for_business
from .models import InventoryLot

logger = logging.getLogger(__name__)


@shared_task
def evaluate_inventory_freshness() -> dict[str, int]:
    """Scan active lots and apply freshness status transitions."""
    updated = 0
    scanned = 0
    for business in Business.objects.filter(status=Business.Status.ACTIVE).iterator():
        policy = policy_for_business(business)
        qs = InventoryLot.objects.filter(
            business=business,
            archived_at__isnull=True,
        ).exclude(
            status__in=[
                InventoryLot.Status.SOLD,
                InventoryLot.Status.DRAFT,
                InventoryLot.Status.EXPIRED,
            ]
        )
        for lot in qs.iterator():
            scanned += 1
            before = lot.status
            apply_freshness_transition(lot, policy=policy)
            if lot.status != before:
                updated += 1
    logger.info("Freshness evaluation scanned=%s updated=%s", scanned, updated)
    return {"scanned": scanned, "updated": updated}
