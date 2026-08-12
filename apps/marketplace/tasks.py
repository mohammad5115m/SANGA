from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.notifications.models import Notification
from apps.notifications.services import notify_user

from .models import SavedSearch
from .selectors import filter_marketplace_lots, marketplace_lots_for

logger = logging.getLogger(__name__)


@shared_task
def match_saved_searches() -> dict[str, int]:
    """Notify users when new lots match their saved B2B searches (deduped)."""
    matched = 0
    notified = 0
    cutoff = timezone.now() - timedelta(days=2)
    searches = SavedSearch.objects.filter(notify_enabled=True).select_related("business", "user")

    for search in searches.iterator():
        query = search.query or {}
        qs = marketplace_lots_for(search.business).filter(created_at__gte=cutoff)
        qs = filter_marketplace_lots(
            qs,
            q=query.get("q", ""),
            stone_type=query.get("stone_type", ""),
            color=query.get("color", ""),
            only_urgent=bool(query.get("only_urgent")),
            min_qty=str(query.get("min_qty", "") or ""),
        )
        # Avoid spam: skip if notified in last 6 hours
        if search.last_notified_at and timezone.now() - search.last_notified_at < timedelta(hours=6):
            continue
        count = qs.count()
        if count <= 0:
            continue
        matched += 1
        notify_user(
            user=search.user,
            business=search.business,
            kind=Notification.Kind.SAVED_SEARCH_MATCH,
            title=f"تطابق جدید: {search.name}",
            body=f"{count} محموله جدید با جستجوی ذخیره‌شده شما هم‌خوانی دارد.",
            link="/app/marketplace/",
        )
        search.last_matched_at = timezone.now()
        search.last_notified_at = timezone.now()
        search.save(update_fields=["last_matched_at", "last_notified_at", "updated_at"])
        notified += 1

    logger.info("Saved search match matched=%s notified=%s", matched, notified)
    return {"matched": matched, "notified": notified}
