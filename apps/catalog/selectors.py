from __future__ import annotations

from django.db.models import Prefetch, Q, QuerySet

from apps.businesses.models import Business
from apps.core.persian import normalize_persian_text
from apps.inventory.models import InventoryLot
from apps.pricing.models import LotPrice

from .models import CustomCatalog, CustomCatalogItem


def _b2c_price_prefetch() -> Prefetch:
    # No to_attr: this populates lot.prices.all() with ONLY B2C rows, so B2B
    # prices are never even loaded on public pages (defense in depth).
    return Prefetch(
        "prices",
        queryset=LotPrice.objects.select_related("tier").filter(tier__code="b2c", tier__is_active=True),
    )


def public_catalog_lots(business: Business) -> QuerySet[InventoryLot]:
    """Lots visible on the B2C storefront. Prefetches only B2C tier prices."""
    return (
        InventoryLot.objects.filter(
            business=business,
            archived_at__isnull=True,
            status__in=[
                InventoryLot.Status.AVAILABLE,
                InventoryLot.Status.NEEDS_CONFIRMATION,
                InventoryLot.Status.PARTIALLY_SOLD,
            ],
            visibility=InventoryLot.Visibility.PUBLIC,
        )
        .select_related("product", "warehouse")
        .prefetch_related(_b2c_price_prefetch(), "media")
        .order_by("-is_urgent_sale", "-updated_at")
    )


def get_public_lot(business: Business, lot_id) -> InventoryLot | None:
    return public_catalog_lots(business).filter(pk=lot_id).first()


def filter_public_lots(
    qs: QuerySet[InventoryLot],
    *,
    q: str = "",
    stone_type: str = "",
    color: str = "",
    only_urgent: bool = False,
) -> QuerySet[InventoryLot]:
    if q:
        term = normalize_persian_text(q)
        qs = qs.filter(
            Q(product__commercial_name__icontains=term)
            | Q(product__stone_type__icontains=term)
            | Q(product__primary_color__icontains=term)
            | Q(lot_code__icontains=term)
            | Q(processing_type__icontains=term)
        )
    if stone_type:
        qs = qs.filter(product__stone_type__icontains=normalize_persian_text(stone_type))
    if color:
        qs = qs.filter(product__primary_color__icontains=normalize_persian_text(color))
    if only_urgent:
        qs = qs.filter(is_urgent_sale=True)
    return qs


def related_public_lots(lot: InventoryLot, *, limit: int = 4) -> list[InventoryLot]:
    return list(
        public_catalog_lots(lot.business)
        .exclude(pk=lot.pk)
        .filter(
            Q(product_id=lot.product_id)
            | Q(product__stone_type=lot.product.stone_type)
            | Q(product__primary_color=lot.product.primary_color)
        )[:limit]
    )


def get_shareable_catalog(token: str) -> CustomCatalog | None:
    catalog = CustomCatalog.objects.select_related("business").filter(share_token=token).first()
    if catalog is None or not catalog.is_publicly_accessible:
        return None
    # Attach items with B2C-safe lot prefetch
    items = (
        CustomCatalogItem.objects.filter(catalog=catalog)
        .select_related("lot__product", "lot__warehouse")
        .prefetch_related(
            Prefetch(
                "lot__prices",
                queryset=LotPrice.objects.select_related("tier").filter(tier__code="b2c", tier__is_active=True),
            ),
            "lot__media",
        )
        .order_by("sort_order")
    )
    catalog.prefetched_items = list(items)
    return catalog


def catalogs_for_business(business: Business) -> QuerySet[CustomCatalog]:
    return CustomCatalog.objects.filter(business=business).prefetch_related("items__lot__product")
