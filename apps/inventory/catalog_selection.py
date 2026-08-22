from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from django.utils import timezone

from .filters import ItemFilterSpec
from .selectors import filter_owned_lots, lots_for_business

SESSION_KEY = "inventory_catalog_selections"
MAX_SELECTIONS = 5
MAX_CATALOG_ITEMS = 500
SELECTION_TTL = timedelta(hours=1)


def create_selection(request, *, business, scope: str, lot_ids: list[str], spec: ItemFilterSpec, state: str) -> str:
    token = secrets.token_urlsafe(18)
    records = request.session.get(SESSION_KEY, {})
    records[token] = {
        "business_id": str(business.pk),
        "scope": "filter" if scope == "filter" else "selected",
        "lot_ids": [str(item) for item in lot_ids],
        "filter": spec.to_dict(),
        "state": state,
        "created_at": timezone.now().isoformat(),
    }
    request.session[SESSION_KEY] = dict(list(records.items())[-MAX_SELECTIONS:])
    request.session.modified = True
    return token


def get_selection(request, *, business, token: str, consume: bool = False) -> dict | None:
    records = request.session.get(SESSION_KEY, {})
    record = records.get(token)
    if not record or record.get("business_id") != str(business.pk):
        return None
    try:
        created_at = datetime.fromisoformat(record["created_at"])
    except (KeyError, TypeError, ValueError):
        return None
    if timezone.is_naive(created_at):
        created_at = timezone.make_aware(created_at)
    if timezone.now() - created_at > SELECTION_TTL:
        records.pop(token, None)
        request.session[SESSION_KEY] = records
        request.session.modified = True
        return None
    if consume:
        records.pop(token, None)
        request.session[SESSION_KEY] = records
        request.session.modified = True
    return record


def resolve_selection(*, business, record: dict):
    qs = lots_for_business(business)
    if record.get("scope") == "filter":
        return filter_owned_lots(
            qs,
            spec=ItemFilterSpec.from_dict(record.get("filter")),
            state=str(record.get("state") or ""),
        )
    return qs.filter(pk__in=record.get("lot_ids") or [])
