from __future__ import annotations

from django.db.models import Prefetch, Q, QuerySet

from apps.businesses.models import Business
from apps.core.persian import normalize_persian_text
from apps.inventory.models import InventoryLot
from apps.partners.models import PartnerRelation
from apps.pricing.models import LotPrice


def approved_supplier_ids(viewer_business: Business) -> list:
    return list(
        PartnerRelation.objects.filter(
            partner_business=viewer_business,
            status=PartnerRelation.Status.APPROVED,
        ).values_list("supplier_business_id", flat=True)
    )


def marketplace_lots_for(viewer_business: Business) -> QuerySet[InventoryLot]:
    """
    B2B marketplace visibility rules:
    - public / all_partners: visible to any marketplace member (active business)
    - selected_partners: only if approved PartnerRelation with supplier
    - never private
    - exclude viewer's own lots
    """
    approved_ids = approved_supplier_ids(viewer_business)
    b2b_prices = LotPrice.objects.select_related("tier").filter(tier__code="b2b", tier__is_active=True)

    visibility_q = Q(
        visibility__in=[
            InventoryLot.Visibility.PUBLIC,
            InventoryLot.Visibility.ALL_PARTNERS,
        ]
    )
    if approved_ids:
        visibility_q |= Q(
            visibility=InventoryLot.Visibility.SELECTED_PARTNERS,
            business_id__in=approved_ids,
        )

    return (
        InventoryLot.objects.filter(
            archived_at__isnull=True,
            status__in=[
                InventoryLot.Status.AVAILABLE,
                InventoryLot.Status.NEEDS_CONFIRMATION,
                InventoryLot.Status.PARTIALLY_SOLD,
            ],
        )
        .exclude(business=viewer_business)
        .filter(visibility_q)
        .select_related("product", "warehouse", "business")
        .prefetch_related(
            # No to_attr: populates lot.prices.all() with ONLY B2B rows so B2C
            # prices are never loaded in marketplace views.
            Prefetch("prices", queryset=b2b_prices),
            "media",
        )
        .order_by("-is_urgent_sale", "-inventory_confirmed_at", "-updated_at")
    )


def get_marketplace_lot(viewer_business: Business, lot_id) -> InventoryLot | None:
    return marketplace_lots_for(viewer_business).filter(pk=lot_id).first()


def filter_marketplace_lots(
    qs: QuerySet[InventoryLot],
    *,
    q: str = "",
    stone_type: str = "",
    color: str = "",
    only_urgent: bool = False,
    only_followed: bool = False,
    followed_supplier_ids: list | None = None,
    min_qty: str = "",
) -> QuerySet[InventoryLot]:
    if q:
        term = normalize_persian_text(q)
        qs = qs.filter(
            Q(product__commercial_name__icontains=term)
            | Q(product__stone_type__icontains=term)
            | Q(product__primary_color__icontains=term)
            | Q(business__name__icontains=term)
            | Q(lot_code__icontains=term)
            | Q(grade__icontains=term)
        )
    if stone_type:
        qs = qs.filter(product__stone_type__icontains=normalize_persian_text(stone_type))
    if color:
        qs = qs.filter(product__primary_color__icontains=normalize_persian_text(color))
    if only_urgent:
        qs = qs.filter(is_urgent_sale=True)
    if only_followed and followed_supplier_ids is not None:
        qs = qs.filter(business_id__in=followed_supplier_ids)
    if min_qty:
        try:
            from decimal import Decimal

            qs = qs.filter(available_sqm__gte=Decimal(min_qty))
        except Exception:
            pass
    return qs


def supplier_directory(exclude_business: Business) -> QuerySet[Business]:
    return (
        Business.objects.filter(status=Business.Status.ACTIVE)
        .exclude(pk=exclude_business.pk)
        .order_by("name")
    )
