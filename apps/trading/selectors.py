from __future__ import annotations

from django.db.models import Q, QuerySet

from apps.businesses.models import Business

from .models import PurchaseRequest, Trade, TradeProposal

_REQUEST_RELATED = (
    "item",
    "item__product",
    "seller_business",
    "buyer_business",
    "created_by",
)


def received_requests(business: Business) -> QuerySet[PurchaseRequest]:
    """«درخواست‌های خرید دریافتی» — other businesses asking to buy from us."""
    return PurchaseRequest.objects.filter(seller_business=business).select_related(*_REQUEST_RELATED)


def sent_requests(business: Business) -> QuerySet[PurchaseRequest]:
    """«درخواست‌های خرید ارسالی» — what we have asked to buy."""
    return PurchaseRequest.objects.filter(buyer_business=business).select_related(*_REQUEST_RELATED)


def get_received_request(business: Business, request_id) -> PurchaseRequest | None:
    return received_requests(business).filter(pk=request_id).first()


def get_sent_request(business: Business, request_id) -> PurchaseRequest | None:
    return sent_requests(business).filter(pk=request_id).first()


def filter_requests(qs: QuerySet[PurchaseRequest], *, status: str = "") -> QuerySet[PurchaseRequest]:
    if status == "open":
        return qs.filter(status__in=PurchaseRequest.OPEN_STATUSES)
    if status:
        return qs.filter(status=status)
    return qs


def trades_for_business(business: Business) -> QuerySet[Trade]:
    # ``items`` is prefetched because every row renders ``summary_label``, which
    # reads the lines. Without it a page of trades is a page of queries.
    return (
        Trade.objects.filter(Q(seller_business=business) | Q(buyer_business=business))
        .select_related("seller_business", "buyer_business", "item")
        .prefetch_related("items")
    )


def trades_for_seller(business: Business) -> QuerySet[Trade]:
    """Compatibility selector for seller-only reports."""
    return trades_for_business(business).filter(seller_business=business)


def proposals_for_business(business: Business) -> QuerySet[TradeProposal]:
    return (
        TradeProposal.objects.filter(Q(seller_business=business) | Q(buyer_business=business))
        .select_related(
            "seller_business",
            "buyer_business",
            "initiated_by_business",
            "created_by",
            "confirmed_by",
            "trade",
        )
        .prefetch_related("items")
    )


def get_proposal(business: Business, proposal_id) -> TradeProposal | None:
    return proposals_for_business(business).filter(pk=proposal_id).first()


def proposals_between(first: Business, second: Business) -> QuerySet[TradeProposal]:
    return proposals_for_business(first).filter(
        Q(seller_business=first, buyer_business=second)
        | Q(seller_business=second, buyer_business=first)
    )


def trades_between(first: Business, second: Business) -> QuerySet[Trade]:
    return trades_for_business(first).filter(
        Q(seller_business=first, buyer_business=second)
        | Q(seller_business=second, buyer_business=first)
    )


def get_trade(business: Business, trade_id) -> Trade | None:
    """A trade is visible to both sides of it, and to nobody else."""
    return (
        Trade.objects.filter(pk=trade_id)
        .filter(models_q(business))
        .select_related("seller_business", "buyer_business", "item", "purchase_request")
        .prefetch_related("items", "invoices")
        .first()
    )


def models_q(business: Business):
    return Q(seller_business=business) | Q(buyer_business=business)
