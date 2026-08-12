from __future__ import annotations

from django.db.models import Prefetch, Q, QuerySet

from apps.businesses.models import Business
from apps.core.persian import normalize_persian_text
from apps.inventory.models import InventoryLot
from apps.pricing.models import ContactPrice, LotPrice

from .models import SavedSearch


def marketplace_lots_for(viewer_business: Business) -> QuerySet[InventoryLot]:
    """
    B2B marketplace visibility rules:
    - both sides must be an active business: a suspended viewer sees nothing and
      a suspended owner's lots (with their B2B prices) are listed to nobody.
      Same notion of "active" as ``contacts.is_linkable_business`` and the
      membership gate in ``businesses.get_active_membership``.
    - colleagues / public: visible to every active business with an account
    - never private
    - exclude viewer's own lots
    """
    if viewer_business is None or viewer_business.status != Business.Status.ACTIVE:
        return InventoryLot.objects.none()

    b2b_prices = LotPrice.objects.select_related("tier").filter(tier__code="b2b", tier__is_active=True)
    # Only this viewer's own overrides, so the prefetch can never carry another
    # colleague's negotiated price into the page.
    viewer_contact_prices = ContactPrice.objects.select_related("contact").filter(
        contact__linked_business=viewer_business,
        contact__is_active=True,
    )

    return (
        InventoryLot.objects.filter(
            # A join on the owning business, not a per-lot lookup: the gate must
            # not cost a query per row.
            business__status=Business.Status.ACTIVE,
            archived_at__isnull=True,
            status__in=[
                InventoryLot.Status.AVAILABLE,
                InventoryLot.Status.NEEDS_CONFIRMATION,
                InventoryLot.Status.PARTIALLY_SOLD,
            ],
            visibility__in=[
                InventoryLot.Visibility.COLLEAGUES,
                InventoryLot.Visibility.PUBLIC,
            ],
        )
        .exclude(business=viewer_business)
        .select_related("product", "warehouse", "business")
        .prefetch_related(
            # No to_attr: populates lot.prices.all() with ONLY B2B rows so B2C
            # prices are never loaded in marketplace views.
            Prefetch("prices", queryset=b2b_prices),
            Prefetch("contact_prices", queryset=viewer_contact_prices),
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
    if min_qty:
        try:
            from decimal import Decimal

            qs = qs.filter(available_sqm__gte=Decimal(min_qty))
        except Exception:
            pass
    return qs


def saved_searches_for(business: Business, user) -> QuerySet[SavedSearch]:
    return SavedSearch.objects.filter(business=business, user=user)
