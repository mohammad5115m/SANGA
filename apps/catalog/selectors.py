from __future__ import annotations

from django.db.models import Prefetch, Q, QuerySet

from apps.businesses.models import Business
from apps.inventory.filters import ItemFilterSpec
from apps.inventory.models import InventoryLot
from apps.inventory.policy import eligible_items, get_eligible_item
from apps.pricing.models import LotPrice

from .models import CustomCatalog, CustomCatalogItem


def public_catalog_lots(business: Business) -> QuerySet[InventoryLot]:
    """Items on one seller's public storefront.

    Thin by design: the eligibility rules live in
    :func:`apps.inventory.policy.eligible_items` so the storefront, share links,
    the marketplace and catalogs cannot disagree about what is public.
    """
    return eligible_items(audience="public", seller_business=business)


def public_items() -> QuerySet[InventoryLot]:
    """Everything publicly discoverable, across all sellers."""
    return eligible_items(audience="public")


def get_public_lot(business: Business, lot_id) -> InventoryLot | None:
    return get_eligible_item(audience="public", seller_business=business, item_id=lot_id)


def get_public_item_by_token(token: str) -> InventoryLot | None:
    return get_eligible_item(audience="public", public_token=token)


def filter_public_lots(qs: QuerySet[InventoryLot], *, spec: ItemFilterSpec) -> QuerySet[InventoryLot]:
    return spec.apply(qs, audience="public")


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
    catalog.prefetched_items = list(resolve_catalog_items(catalog))
    return catalog


def resolve_catalog_items(catalog: CustomCatalog) -> QuerySet[CustomCatalogItem]:
    """Manually-selected catalog entries that are currently publicly showable.

    Intersecting with the public eligibility queryset is what stops a curated
    link from widening visibility. A hidden, unavailable or deleted item drops
    out of the catalog the moment it changes, without anyone editing the
    catalog.
    """
    publishable = eligible_items(audience="public", seller_business=catalog.business).order_by().values("pk")
    return (
        CustomCatalogItem.objects.filter(catalog=catalog, lot__in=publishable)
        .select_related("lot__product", "lot__business")
        .prefetch_related(
            Prefetch(
                "lot__prices",
                queryset=LotPrice.objects.select_related("tier").filter(tier__code="b2c", tier__is_active=True),
            ),
            "lot__media",
        )
        .order_by("sort_order")
    )


def catalogs_for_business(business: Business) -> QuerySet[CustomCatalog]:
    return CustomCatalog.objects.filter(business=business).prefetch_related("items__lot__product")
