from __future__ import annotations

from django.db.models import QuerySet

from apps.businesses.models import Business
from apps.marketplace.selectors import marketplace_lots_for
from apps.purchase_requests.models import PurchaseRequest

from .models import MatchResult


def visible_matches_for(
    purchase_request: PurchaseRequest,
    viewer_business: Business,
) -> QuerySet[MatchResult]:
    """
    MatchResult rows are a snapshot taken at matching time, so a revoked partnership
    or a lot turned private/archived would keep leaking the supplier and product name
    until the next rematch. Re-checking every row against the current marketplace gate
    at read time is correct by construction, unlike invalidation on change.

    The gate is applied as a single `IN (subquery)`, so it costs no query per match.
    """
    visible_lot_ids = marketplace_lots_for(viewer_business).order_by().values("pk")
    return (
        MatchResult.objects.filter(purchase_request=purchase_request, lot_id__in=visible_lot_ids)
        .select_related("lot", "lot__product", "lot__business")
        .order_by("-score")
    )
