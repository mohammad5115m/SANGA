from __future__ import annotations

from django.db.models import Prefetch, QuerySet

from apps.businesses.models import Business

from .models import PurchaseOffer, PurchaseRequest


def my_purchase_requests(business: Business) -> QuerySet[PurchaseRequest]:
    return (
        PurchaseRequest.objects.filter(business=business)
        .prefetch_related(
            Prefetch(
                "offers",
                queryset=PurchaseOffer.objects.select_related("seller_business", "lot"),
            ),
            "match_results__lot__product",
        )
        .order_by("-created_at")
    )


def network_purchase_requests(viewer_business: Business) -> QuerySet[PurchaseRequest]:
    """Open network demand visible to other businesses (not a public auction)."""
    return (
        PurchaseRequest.objects.filter(
            is_public_to_network=True,
            status__in=[
                PurchaseRequest.Status.OPEN,
                PurchaseRequest.Status.MATCHING,
                PurchaseRequest.Status.OFFERED,
            ],
        )
        .exclude(business=viewer_business)
        .select_related("business")
        .order_by("-created_at")
    )


def get_own_request(business: Business, pr_id) -> PurchaseRequest | None:
    return my_purchase_requests(business).filter(pk=pr_id).first()


def get_network_request(viewer_business: Business, pr_id) -> PurchaseRequest | None:
    return network_purchase_requests(viewer_business).filter(pk=pr_id).first()


def offers_for_requester(pr: PurchaseRequest) -> QuerySet[PurchaseOffer]:
    return pr.offers.select_related("seller_business", "lot", "lot__product").order_by("-created_at")


def my_offer_for(pr: PurchaseRequest, seller_business: Business) -> PurchaseOffer | None:
    return (
        PurchaseOffer.objects.filter(purchase_request=pr, seller_business=seller_business)
        .select_related("lot")
        .order_by("-created_at")
        .first()
    )
