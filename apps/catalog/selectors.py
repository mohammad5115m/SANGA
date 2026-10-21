from __future__ import annotations

from django.db.models import Count, Prefetch, Q, QuerySet, Window

from apps.businesses.eligibility import business_can_sell
from apps.businesses.models import Business
from apps.inventory.filters import ItemFilterSpec
from apps.inventory.models import InventoryLot
from apps.inventory.policy import eligible_items, get_eligible_item
from apps.pricing.models import LotPrice
from apps.pricing.queries import live_special_until_subquery

from .models import CustomCatalog, StorefrontCollection, StorefrontCollectionItem

__all__ = [
    "public_catalog_lots",
    "get_public_lot",
    "get_public_item_by_token",
    "filter_public_lots",
    "related_public_lots",
    "get_shareable_catalog",
    "resolve_catalog",
    "selected_catalog_lots",
    "catalog_notes",
    "catalogs_for_business",
    "active_special_lots",
    "storefront_collection_sections",
]


def public_catalog_lots(business: Business) -> QuerySet[InventoryLot]:
    """Items on one seller's public storefront.

    Thin by design: the eligibility rules live in
    :func:`apps.inventory.policy.eligible_items` so the storefront, share links,
    the marketplace and catalogs cannot disagree about what is public.
    """
    return eligible_items(audience="public", seller_business=business)


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
            Q(product_id=lot.product_id) | Q(product__stone_id=lot.product.stone_id)
        )[:limit]
    )


def get_shareable_catalog(token: str) -> CustomCatalog | None:
    """A shared catalog link, or nothing.

    Gated on the seller as well as the catalog. The catalog's own switches —
    active, not expired — used to be the whole test, so a seller who was
    suspended or whose subscription lapsed kept a live public link carrying their
    name and an empty product list, because ``resolve_catalog`` filtered the
    products out one layer down. A shared link is a public page like any other
    and answers to the same rule.
    """
    catalog = CustomCatalog.objects.select_related("business").filter(share_token=token).first()
    if catalog is None or not catalog.is_publicly_accessible:
        return None
    if not business_can_sell(catalog.business):
        return None
    catalog.resolved_items = resolve_catalog(catalog)
    return catalog


def resolve_catalog(catalog: CustomCatalog) -> QuerySet[InventoryLot]:
    """The products this catalog shows *right now*.

    ```text
    selected products INTERSECT currently eligible products
    ```

    The intersection is the important half. It is what stops a curated link from
    widening visibility: a product that becomes hidden, unavailable or deleted
    leaves every catalog immediately, and comes back on its own if it becomes
    available again and still matches. Nobody has to re-curate anything.

    Evaluated at read time, **in the database**, and returned as a queryset so a
    large catalog can be paged rather than loaded whole. The rule matches used to
    be pulled into a Python ``set`` of primary keys before the manual includes
    were applied, which meant the cost of rendering page one grew with the size
    of the entire match.

    Membership is explicit; the values rendered for each selected item stay live.
    """
    eligible = eligible_items(audience="public", seller_business=catalog.business)

    return eligible.filter(custom_catalog_items__catalog=catalog).order_by(
        "custom_catalog_items__sort_order",
        "custom_catalog_items__id",
        "-updated_at",
    )


def selected_catalog_lots(catalog: CustomCatalog) -> QuerySet[InventoryLot]:
    """Every still-owned selection for management, regardless of publication."""
    return (
        InventoryLot.objects.filter(
            business=catalog.business,
            deleted_at__isnull=True,
            custom_catalog_items__catalog=catalog,
        )
        .select_related("product", "product__stone")
        .order_by("custom_catalog_items__sort_order", "custom_catalog_items__id")
    )


def catalog_notes(catalog: CustomCatalog) -> dict:
    """Per-product notes the seller attached, keyed by product id."""
    return {
        str(lot_id): note
        for lot_id, note in catalog.items.exclude(note="").values_list("lot_id", "note")
    }


def catalogs_for_business(business: Business) -> QuerySet[CustomCatalog]:
    return (
        CustomCatalog.objects.filter(business=business)
        .annotate(item_count=Count("items"))
        .order_by("title")
    )


def active_special_lots(
    business: Business, *, limit: int | None = 12
) -> QuerySet[InventoryLot]:
    """Current B2C promotions for this seller only, ending soonest first."""
    queryset = (
        public_catalog_lots(business)
        .annotate(_special_until=live_special_until_subquery("b2c"))
        .filter(_special_until__isnull=False)
        .annotate(_special_total=Window(expression=Count("pk")))
        .order_by("_special_until", "-updated_at")
    )
    return queryset[:limit] if limit is not None else queryset


def storefront_collection_sections(business: Business) -> QuerySet[StorefrontCollection]:
    """Visible collections with eligible, tenant-scoped products prefetched."""
    eligible_ids = public_catalog_lots(business).values("pk")
    memberships = (
        StorefrontCollectionItem.objects.filter(lot__in=eligible_ids)
        .select_related("lot", "lot__business", "lot__product", "lot__product__stone")
        .prefetch_related(
            Prefetch(
                "lot__prices",
                queryset=LotPrice.objects.select_related("tier").filter(
                    tier__code="b2c", tier__is_active=True
                ),
            ),
            "lot__media",
            "lot__product__applications",
        )
        .order_by("sort_order", "id")
    )
    return (
        StorefrontCollection.objects.filter(business=business, is_active=True)
        .prefetch_related(Prefetch("items", queryset=memberships, to_attr="public_items"))
        .order_by("sort_order", "created_at")
    )
