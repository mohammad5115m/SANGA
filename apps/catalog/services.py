from __future__ import annotations

import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone

from apps.businesses.entitlements import MANAGE_CATALOGS, EntitlementError, require_entitlement
from apps.businesses.models import Business, BusinessMembership
from apps.businesses.permissions import CATALOG_MANAGE
from apps.inventory.freshness import stock_view
from apps.inventory.models import InventoryLot
from apps.pricing.services import resolve_visible_prices

from .models import CustomCatalog, CustomCatalogItem

logger = logging.getLogger(__name__)


class CatalogError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def _require_catalog_manage(membership: BusinessMembership) -> None:
    if membership is None or not membership.has_capability(CATALOG_MANAGE):
        raise CatalogError("اجازه مدیریت کاتالوگ را ندارید.")
    try:
        require_entitlement(membership.business, MANAGE_CATALOGS)
    except EntitlementError as exc:
        raise CatalogError(exc.message) from exc


def b2c_price_context(lot: InventoryLot) -> dict:
    """Public price payload: B2C only, flat, never carrying a tier map.

    Returning a plain dict rather than the resolved tier dict means a template
    has nothing to walk even if someone tries. An expired or inquiry-mode price
    arrives here already reduced to «استعلام قیمت».
    """
    prices = resolve_visible_prices(lot, "b2c_public")
    b2c = prices.get("b2c")
    if b2c is None or b2c.amount is None:
        return {
            "has_price": False,
            "amount": None,
            "currency": None,
            "unit": None,
            "label": "استعلام قیمت",
            "is_special": False,
        }
    return {
        "has_price": True,
        "amount": b2c.amount,
        "currency": b2c.currency,
        "unit": b2c.unit,
        "label": f"{b2c.amount:,.0f} {b2c.currency}",
        "is_special": b2c.is_special,
        "special_until": b2c.special_until,
    }


def public_lot_card(lot: InventoryLot) -> dict:
    primary = next((m for m in lot.media.all() if m.is_primary), None) or next(iter(lot.media.all()), None)
    return {
        "lot": lot,
        "product": lot.product,
        "price": b2c_price_context(lot),
        "stock": stock_view(lot),
        "primary_media": primary,
    }


@transaction.atomic
def create_custom_catalog(
    *,
    business: Business,
    membership: BusinessMembership,
    title: str,
    customer_name: str = "",
    custom_message: str = "",
    lot_ids: list | None = None,
    expires_at=None,
) -> CustomCatalog:
    _require_catalog_manage(membership)
    if membership.business_id != business.id:
        raise CatalogError("دسترسی نامعتبر است.")
    title = (title or "").strip()
    if len(title) < 2:
        raise CatalogError("عنوان کاتالوگ خیلی کوتاه است.")

    catalog = CustomCatalog.objects.create(
        business=business,
        title=title,
        customer_name=(customer_name or "").strip(),
        custom_message=(custom_message or "").strip(),
        expires_at=expires_at,
    )
    if lot_ids:
        set_catalog_lots(catalog=catalog, membership=membership, lot_ids=lot_ids)
    return catalog


@transaction.atomic
def set_catalog_lots(
    *,
    catalog: CustomCatalog,
    membership: BusinessMembership,
    lot_ids: list,
) -> CustomCatalog:
    """Replace the catalog's manual selection with ``lot_ids``.

    Only items the acting business owns and has not deleted may be attached. An
    id from another tenant — or one that is simply not a valid id — aborts the
    whole call, so the caller cannot be told a crafted request succeeded.

    Note what is *not* checked: visibility and availability. A seller may put a
    currently-hidden item in a catalog while preparing it. Whether it renders is
    decided at read time by ``resolve_catalog_items``, which intersects the
    selection with the public eligibility queryset.
    """
    _require_catalog_manage(membership)
    if catalog.business_id != membership.business_id:
        raise CatalogError("دسترسی به این کاتالوگ وجود ندارد.")

    requested = [lot_id for lot_id in (lot_ids or []) if lot_id is not None]
    try:
        owned = list(
            InventoryLot.objects.filter(
                business=catalog.business,
                id__in=requested,
                deleted_at__isnull=True,
            ).values_list("id", flat=True)
        )
    except (DjangoValidationError, TypeError, ValueError) as exc:
        raise CatalogError("محصول انتخاب‌شده معتبر نیست.") from exc

    # Reject rather than silently drop: an id this business does not own must
    # never be accepted as a no-op, or a crafted request looks like it worked.
    if len(owned) != len({str(lot_id) for lot_id in requested}):
        raise CatalogError("یک یا چند محصول انتخاب‌شده متعلق به کسب‌وکار شما نیست.")

    owned_ids = {str(lot_id) for lot_id in owned}
    valid_ids: list = []
    seen: set[str] = set()
    for lot_id in requested:
        key = str(lot_id)
        if key in owned_ids and key not in seen:
            seen.add(key)
            valid_ids.append(lot_id)

    catalog.items.all().delete()
    CustomCatalogItem.objects.bulk_create(
        [
            CustomCatalogItem(catalog=catalog, lot_id=lot_id, sort_order=index)
            for index, lot_id in enumerate(valid_ids)
        ]
    )
    catalog.save(update_fields=["updated_at"])
    return catalog


@transaction.atomic
def record_catalog_view(catalog: CustomCatalog) -> CustomCatalog:
    now = timezone.now()
    # F() rather than read-modify-write: two visitors landing at once must not
    # lose a count.
    from django.db.models import F

    CustomCatalog.objects.filter(pk=catalog.pk).update(
        view_count=F("view_count") + 1,
        last_viewed_at=now,
        updated_at=now,
    )
    CustomCatalog.objects.filter(pk=catalog.pk, first_viewed_at__isnull=True).update(first_viewed_at=now)
    catalog.refresh_from_db()
    return catalog
