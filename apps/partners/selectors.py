from __future__ import annotations

from django.db.models import QuerySet

from apps.businesses.models import Business

from .models import PartnerRelation, SavedSearch, SupplierFollow


def incoming_requests(supplier_business: Business) -> QuerySet[PartnerRelation]:
    return (
        PartnerRelation.objects.filter(
            supplier_business=supplier_business,
            status=PartnerRelation.Status.REQUESTED,
        )
        .select_related("partner_business")
        .order_by("-created_at")
    )


def outgoing_relations(partner_business: Business) -> QuerySet[PartnerRelation]:
    return (
        PartnerRelation.objects.filter(partner_business=partner_business)
        .select_related("supplier_business")
        .order_by("-updated_at")
    )


def approved_partners_for_supplier(supplier_business: Business) -> QuerySet[PartnerRelation]:
    return PartnerRelation.objects.filter(
        supplier_business=supplier_business,
        status=PartnerRelation.Status.APPROVED,
    ).select_related("partner_business")


def followed_supplier_ids(follower_business: Business) -> list:
    return list(
        SupplierFollow.objects.filter(follower_business=follower_business).values_list(
            "supplier_business_id", flat=True
        )
    )


def saved_searches_for(business: Business, user) -> QuerySet[SavedSearch]:
    return SavedSearch.objects.filter(business=business, user=user)
