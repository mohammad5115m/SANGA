from __future__ import annotations

from django.db.models import Prefetch, Q, QuerySet

from apps.businesses.models import Business
from apps.core.persian import normalize_persian_text
from apps.pricing.models import LotPrice

from .models import InventoryLot, Product


def products_for_business(business: Business) -> QuerySet[Product]:
    return Product.objects.filter(business=business, is_active=True).order_by("commercial_name")


def lots_for_business(business: Business) -> QuerySet[InventoryLot]:
    return (
        InventoryLot.objects.filter(business=business, archived_at__isnull=True)
        .select_related("product", "warehouse")
        .prefetch_related(
            Prefetch("prices", queryset=LotPrice.objects.select_related("tier")),
            "media",
        )
        .order_by("-updated_at")
    )


def get_business_lot(business: Business, lot_id) -> InventoryLot | None:
    return lots_for_business(business).filter(pk=lot_id).first()


def filter_lots(
    qs: QuerySet[InventoryLot],
    *,
    q: str = "",
    status: str = "",
    visibility: str = "",
    freshness: str = "",
) -> QuerySet[InventoryLot]:
    if q:
        term = normalize_persian_text(q)
        qs = qs.filter(
            Q(lot_code__icontains=term)
            | Q(product__commercial_name__icontains=term)
            | Q(product__stone_type__icontains=term)
            | Q(product__primary_color__icontains=term)
            | Q(grade__icontains=term)
        )
    if status:
        qs = qs.filter(status=status)
    if visibility:
        qs = qs.filter(visibility=visibility)
    if freshness == "needs_confirmation":
        qs = qs.filter(status=InventoryLot.Status.NEEDS_CONFIRMATION)
    elif freshness == "urgent":
        qs = qs.filter(is_urgent_sale=True)
    elif freshness == "draft":
        qs = qs.filter(status=InventoryLot.Status.DRAFT)
    return qs
