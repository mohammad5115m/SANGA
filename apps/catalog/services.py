from __future__ import annotations

import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone

from apps.businesses.models import Business, BusinessMembership
from apps.businesses.permissions import CATALOG_MANAGE
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


def b2c_price_context(lot: InventoryLot) -> dict:
    """Safe public price payload: B2C only, never includes B2B keys."""
    prices = resolve_visible_prices(lot, "b2c_public")
    b2c = prices.get("b2c")
    if b2c is None:
        return {"has_price": False, "amount": None, "currency": None, "unit": None, "label": "استعلام بگیرید"}
    if b2c.display_as_inquiry or b2c.amount is None:
        return {
            "has_price": False,
            "amount": None,
            "currency": b2c.currency,
            "unit": b2c.unit,
            "label": "استعلام بگیرید",
        }
    return {
        "has_price": True,
        "amount": b2c.amount,
        "currency": b2c.currency,
        "unit": b2c.unit,
        "label": f"{b2c.amount:,.0f} {b2c.currency}",
    }


def public_lot_card(lot: InventoryLot) -> dict:
    primary = next((m for m in lot.media.all() if m.is_primary), None) or next(iter(lot.media.all()), None)
    return {
        "lot": lot,
        "product": lot.product,
        "price": b2c_price_context(lot),
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
    """Replace the catalog's lots with ``lot_ids``.

    Only lots the acting business owns and has not archived may be attached. A
    lot id from another tenant — or one that is simply not a valid id — aborts
    the whole call, so the caller cannot be told a crafted request succeeded.
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
                archived_at__isnull=True,
            ).values_list("id", flat=True)
        )
    except (DjangoValidationError, TypeError, ValueError) as exc:
        # A malformed id can only come from a crafted request; refuse the whole
        # submission rather than quietly attaching the well-formed remainder.
        raise CatalogError("محموله انتخاب‌شده معتبر نیست.") from exc

    # Reject rather than silently drop: a lot id this business does not own must
    # never be accepted as a no-op, or a crafted request looks like it worked.
    if len(owned) != len({str(lot_id) for lot_id in requested}):
        raise CatalogError("یک یا چند محموله انتخاب‌شده متعلق به کسب‌وکار شما نیست.")

    # Preserve the order the caller asked for; the DB does not guarantee it.
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
    catalog.view_count = (catalog.view_count or 0) + 1
    if catalog.first_viewed_at is None:
        catalog.first_viewed_at = now
    catalog.last_viewed_at = now
    catalog.save(update_fields=["view_count", "first_viewed_at", "last_viewed_at", "updated_at"])
    return catalog

