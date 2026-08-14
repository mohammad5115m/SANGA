from __future__ import annotations

from django.db.models import QuerySet

from apps.businesses.models import Business

from .filters import ItemFilterSpec
from .models import InventoryLot, Product
from .policy import owned_items


def products_for_business(business: Business) -> QuerySet[Product]:
    return (
        Product.objects.filter(business=business, is_active=True)
        .select_related("stone")
        .prefetch_related("applications")
        .order_by("commercial_name")
    )


def lots_for_business(business: Business) -> QuerySet[InventoryLot]:
    """Everything the seller may manage, including hidden and unavailable items.

    Owner-side listing deliberately does *not* go through
    :func:`~apps.inventory.policy.eligible_items`: a seller has to be able to
    find an item precisely when it has dropped off the buyer-facing surfaces.
    """
    return owned_items(business)


def get_business_lot(business: Business, lot_id) -> InventoryLot | None:
    return lots_for_business(business).filter(pk=lot_id).first()


#: Owner-side lifecycle filters, expressed in the seller's language rather than
#: in model fields. These sit outside ItemFilterSpec because they ask about
#: management state, which no buyer-facing surface may filter on.
OWNER_STATE_CHOICES: tuple[tuple[str, str], ...] = (
    ("", "همه"),
    ("available", "موجود"),
    ("unavailable", "ناموجود"),
    ("hidden", "منتشر نشده"),
    ("draft", "پیش‌نویس"),
    ("needs_stock", "نیازمند تأیید موجودی"),
)


def filter_owned_lots(
    qs: QuerySet[InventoryLot],
    *,
    spec: ItemFilterSpec | None = None,
    state: str = "",
) -> QuerySet[InventoryLot]:
    """Apply the shared filter schema plus the owner-only lifecycle filter."""
    if spec is not None:
        qs = spec.apply(qs, audience="owner")

    if state == "available":
        qs = qs.filter(availability_status=InventoryLot.Availability.AVAILABLE)
    elif state == "unavailable":
        qs = qs.filter(availability_status=InventoryLot.Availability.UNAVAILABLE)
    elif state == "hidden":
        qs = qs.filter(is_visible=False, status=InventoryLot.Status.ACTIVE)
    elif state == "draft":
        qs = qs.filter(status=InventoryLot.Status.DRAFT)
    elif state == "needs_stock":
        qs = filter_needs_stock_confirmation(qs)
    return qs


def filter_needs_stock_confirmation(qs: QuerySet[InventoryLot]) -> QuerySet[InventoryLot]:
    """Items carrying a quantity the seller has stopped vouching for."""
    return qs.filter(InventoryLot.needs_stock_confirmation_q())
