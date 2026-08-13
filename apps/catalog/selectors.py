from __future__ import annotations

from django.db.models import Q, QuerySet

from apps.businesses.models import Business
from apps.inventory.filters import ItemFilterSpec
from apps.inventory.models import InventoryLot
from apps.inventory.policy import eligible_items, get_eligible_item

from .models import CustomCatalog, CustomCatalogItem

__all__ = [
    "public_catalog_lots",
    "public_items",
    "get_public_lot",
    "get_public_item_by_token",
    "filter_public_lots",
    "related_public_lots",
    "get_shareable_catalog",
    "resolve_catalog",
    "catalog_notes",
    "catalogs_for_business",
]


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
    catalog.resolved_items = resolve_catalog(catalog)
    return catalog


def resolve_catalog(catalog: CustomCatalog) -> list[InventoryLot]:
    """The products this catalog shows *right now*.

    ```text
    (products matching the rules + manual includes - manual excludes)
        INTERSECT currently eligible products
    ```

    The intersection is the important half. It is what stops a curated link from
    widening visibility: a product that becomes hidden, unavailable or deleted
    leaves every catalog immediately, and comes back on its own if it becomes
    available again and still matches. Nobody has to re-curate anything.

    Evaluated at read time, in the database. A rule catalog is live by
    definition — a new matching product appears without the seller touching the
    catalog — so there is nothing to cache and nothing to invalidate.
    """
    eligible = eligible_items(audience="public", seller_business=catalog.business)

    overrides = list(catalog.items.values_list("lot_id", "inclusion"))
    include_ids = [pk for pk, kind in overrides if kind == CustomCatalogItem.Inclusion.INCLUDE]
    exclude_ids = [pk for pk, kind in overrides if kind == CustomCatalogItem.Inclusion.EXCLUDE]

    if catalog.uses_rules:
        spec = ItemFilterSpec.from_dict(catalog.rules)
        selected = spec.apply(eligible, audience="public")
        if include_ids:
            # OR the manual additions back in: they are exceptions to the rule,
            # not further narrowing of it.
            selected = eligible.filter(Q(pk__in=set(selected.values_list("pk", flat=True))) | Q(pk__in=include_ids))
    else:
        selected = eligible.filter(pk__in=include_ids)

    if exclude_ids:
        selected = selected.exclude(pk__in=exclude_ids)

    # No extra prefetch: eligible_items already loaded the B2C tier and media,
    # and adding a second `prices` prefetch would conflict with it.
    ordered = selected.distinct()

    if catalog.mode == CustomCatalog.Mode.MANUAL:
        # A hand-picked catalog is a presentation, so the seller's ordering wins.
        position = {
            str(pk): index
            for index, pk in enumerate(
                catalog.items.filter(inclusion=CustomCatalogItem.Inclusion.INCLUDE)
                .order_by("sort_order", "id")
                .values_list("lot_id", flat=True)
            )
        }
        return sorted(ordered, key=lambda item: position.get(str(item.pk), 9999))
    return list(ordered)


def catalog_notes(catalog: CustomCatalog) -> dict:
    """Per-product notes the seller attached, keyed by product id."""
    return {
        str(lot_id): note
        for lot_id, note in catalog.items.exclude(note="").values_list("lot_id", "note")
    }


def catalogs_for_business(business: Business) -> QuerySet[CustomCatalog]:
    return CustomCatalog.objects.filter(business=business).prefetch_related("items__lot__product")
