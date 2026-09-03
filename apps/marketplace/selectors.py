from __future__ import annotations

from django.db.models import QuerySet
from django.utils import timezone

from apps.businesses.eligibility import business_can_sell
from apps.businesses.models import Business
from apps.inventory.filters import ItemFilterSpec
from apps.inventory.models import InventoryLot
from apps.inventory.policy import eligible_items, owned_items
from apps.pricing.queries import effective_amount_subquery


def transaction_ready_lots(qs: QuerySet[InventoryLot]) -> QuerySet[InventoryLot]:
    """Keep only products whose B2B price and exact stock are current.

    The colleague marketplace is a catalogue of products that can be invoiced
    now, not a queue for asking sellers to reconfirm data they already own.
    """
    return (
        qs.filter(
            available_sqm__gt=0,
            stock_expires_at__gt=timezone.now(),
        )
        .annotate(_marketplace_b2b_price=effective_amount_subquery("b2b"))
        .filter(_marketplace_b2b_price__isnull=False)
    )


def marketplace_lots_for(viewer_business: Business) -> QuerySet[InventoryLot]:
    """Transaction-ready items other eligible colleagues are offering."""
    return transaction_ready_lots(
        eligible_items(audience="colleague", viewer_business=viewer_business)
    )


def marketplace_ready_items_for_owner(business: Business) -> QuerySet[InventoryLot]:
    """Owner-side readiness check without excluding the owner's own products."""
    if not business_can_sell(business):
        return InventoryLot.objects.none()
    return transaction_ready_lots(
        owned_items(business).filter(
            product__is_active=True,
            is_visible=True,
            availability_status=InventoryLot.Availability.AVAILABLE,
            status=InventoryLot.Status.ACTIVE,
        )
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
