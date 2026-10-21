from __future__ import annotations

from django.db.models import QuerySet

from apps.businesses.eligibility import business_can_sell
from apps.businesses.models import Business
from apps.inventory.filters import ItemFilterSpec
from apps.inventory.models import InventoryLot
from apps.inventory.policy import eligible_items, owned_items


def marketplace_lots_for(viewer_business: Business) -> QuerySet[InventoryLot]:
    """Published products from eligible colleagues, including inquiry prices.

    Expired confirmations remove the *number*, not the product. This keeps
    discovery and detail/share links consistent with the public storefront.
    """
    return eligible_items(audience="colleague", viewer_business=viewer_business)


def marketplace_ready_items_for_owner(business: Business) -> QuerySet[InventoryLot]:
    """Owner-side visibility check without excluding the owner's own products."""
    if not business_can_sell(business):
        return InventoryLot.objects.none()
    return owned_items(business).filter(
        product__is_active=True,
        is_visible=True,
        availability_status=InventoryLot.Availability.AVAILABLE,
        status=InventoryLot.Status.ACTIVE,
    )


def get_marketplace_lot(viewer_business: Business, lot_id) -> InventoryLot | None:
    return marketplace_lots_for(viewer_business).filter(pk=lot_id).first()


def get_marketplace_lot_by_token(
    viewer_business: Business, public_token: str
) -> InventoryLot | None:
    return marketplace_lots_for(viewer_business).filter(public_token=public_token).first()


def filter_marketplace_lots(
    qs: QuerySet[InventoryLot], *, spec: ItemFilterSpec
) -> QuerySet[InventoryLot]:
    return spec.apply(qs, audience="colleague")
