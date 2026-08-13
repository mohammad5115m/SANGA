from __future__ import annotations

from django.db.models import QuerySet

from apps.businesses.models import Business
from apps.inventory.filters import ItemFilterSpec
from apps.inventory.models import InventoryLot
from apps.inventory.policy import eligible_items, get_eligible_item


def marketplace_lots_for(viewer_business: Business) -> QuerySet[InventoryLot]:
    """Items other colleagues are offering.

    All the visibility reasoning lives in :func:`apps.inventory.policy.eligible_items`,
    including the rule that a suspended business neither sees the marketplace nor
    appears in it. This function exists only to name the audience.
    """
    return eligible_items(audience="colleague", viewer_business=viewer_business)


def get_marketplace_lot(viewer_business: Business, lot_id) -> InventoryLot | None:
    return get_eligible_item(
        audience="colleague",
        viewer_business=viewer_business,
        item_id=lot_id,
    )


def filter_marketplace_lots(qs: QuerySet[InventoryLot], *, spec: ItemFilterSpec) -> QuerySet[InventoryLot]:
    return spec.apply(qs, audience="colleague")
