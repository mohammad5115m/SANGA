from __future__ import annotations

import logging
import secrets
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from apps.businesses.eligibility import NotOperationalError, require_operational
from apps.businesses.entitlements import MANAGE_CATALOGS, EntitlementError, require_entitlement
from apps.businesses.models import Business, BusinessMembership
from apps.businesses.permissions import CATALOG_MANAGE
from apps.core.formatting import format_rial
from apps.inventory.freshness import stock_view
from apps.inventory.models import InventoryLot
from apps.pricing.services import resolve_visible_prices

from .models import (
    CustomCatalog,
    CustomCatalogItem,
    StorefrontCollection,
    StorefrontCollectionItem,
)

logger = logging.getLogger(__name__)


class CatalogError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def _require_catalog_manage(membership: BusinessMembership) -> None:
    if membership is None or not membership.has_capability(CATALOG_MANAGE):
        raise CatalogError("اجازه مدیریت کاتالوگ را ندارید.")
    try:
        require_operational(membership.business)
        require_entitlement(membership.business, MANAGE_CATALOGS)
    except (NotOperationalError, EntitlementError) as exc:
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
            "label": "استعلام قیمت",
            "is_special": False,
            "regular_amount": None,
            "regular_label": "",
            "discount_percent": None,
            "remaining_label": "",
        }
    regular_amount = b2c.regular_amount if b2c.is_special else None
    discount_percent = None
    if regular_amount and b2c.amount:
        raw_discount = ((regular_amount - b2c.amount) / regular_amount) * Decimal("100")
        # A non-zero payable price must never be advertised as «100% off».
        discount_percent = min(
            99,
            max(0, int(raw_discount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))),
        )
    return {
        "has_price": True,
        "amount": b2c.amount,
        "currency": b2c.currency,
        "label": format_rial(b2c.amount),
        "is_special": b2c.is_special,
        "special_until": b2c.special_until,
        "regular_amount": regular_amount,
        "regular_label": format_rial(regular_amount) if regular_amount else "",
        "discount_percent": discount_percent,
        "remaining_label": _promotion_remaining(b2c.special_until),
    }


def _promotion_remaining(ends_at) -> str:
    if ends_at is None:
        return ""
    seconds = max(0, int((ends_at - timezone.now()).total_seconds()))
    days, remainder = divmod(seconds, 86400)
    hours = remainder // 3600
    if days:
        return f"{days} روز و {hours} ساعت تا پایان پیشنهاد"
    if hours:
        return f"{hours} ساعت تا پایان پیشنهاد"
    minutes = max(1, seconds // 60)
    return f"{minutes} دقیقه تا پایان پیشنهاد"


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
def update_catalog(
    *,
    catalog: CustomCatalog,
    membership: BusinessMembership,
    title: str | None = None,
    customer_name: str | None = None,
    custom_message: str | None = None,
    expires_at=...,
    is_active: bool | None = None,
) -> CustomCatalog:
    _require_catalog_manage(membership)
    if catalog.business_id != membership.business_id:
        raise CatalogError("دسترسی به این کاتالوگ وجود ندارد.")

    if title is not None:
        title = title.strip()
        if len(title) < 2:
            raise CatalogError("عنوان کاتالوگ خیلی کوتاه است.")
        catalog.title = title
    if customer_name is not None:
        catalog.customer_name = customer_name.strip()
    if custom_message is not None:
        catalog.custom_message = custom_message.strip()
    if expires_at is not ...:
        catalog.expires_at = expires_at
    if is_active is not None:
        catalog.is_active = is_active
    catalog.save()
    return catalog


def _owned_ids(catalog: CustomCatalog, lot_ids: list) -> list:
    """Validate that every id belongs to this catalog's business.

    Rejects rather than silently drops: an id this business does not own must
    never be accepted as a no-op, or a crafted request looks like it worked.
    """
    requested = [lot_id for lot_id in (lot_ids or []) if lot_id is not None]
    if not requested:
        return []
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

    if len(owned) != len({str(lot_id) for lot_id in requested}):
        raise CatalogError("یک یا چند محصول انتخاب‌شده متعلق به کسب‌وکار شما نیست.")

    owned_ids = {str(lot_id) for lot_id in owned}
    ordered: list = []
    seen: set[str] = set()
    for lot_id in requested:
        key = str(lot_id)
        if key in owned_ids and key not in seen:
            seen.add(key)
            ordered.append(lot_id)
    return ordered


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

    valid_ids = _owned_ids(catalog, lot_ids)

    catalog.items.all().delete()
    CustomCatalogItem.objects.bulk_create(
        [
            CustomCatalogItem(
                catalog=catalog,
                lot_id=lot_id,
                sort_order=index,
            )
            for index, lot_id in enumerate(valid_ids)
        ]
    )
    catalog.save(update_fields=["updated_at"])
    return catalog


@transaction.atomic
def add_catalog_lots(*, catalog: CustomCatalog, membership: BusinessMembership, lot_ids: list) -> CustomCatalog:
    _require_catalog_manage(membership)
    if catalog.business_id != membership.business_id:
        raise CatalogError("دسترسی به این کاتالوگ وجود ندارد.")
    valid_ids = _owned_ids(catalog, lot_ids)
    existing = list(catalog.items.order_by("sort_order", "id"))
    seen = {str(item.lot_id) for item in existing}
    additions = [item for item in valid_ids if str(item) not in seen]
    CustomCatalogItem.objects.bulk_create(
        [
            CustomCatalogItem(catalog=catalog, lot_id=lot_id, sort_order=len(existing) + index)
            for index, lot_id in enumerate(additions)
        ]
    )
    if additions:
        catalog.save(update_fields=["updated_at"])
    return catalog


@transaction.atomic
def remove_catalog_lot(*, catalog: CustomCatalog, membership: BusinessMembership, lot_id) -> CustomCatalog:
    _require_catalog_manage(membership)
    if catalog.business_id != membership.business_id:
        raise CatalogError("دسترسی به این کاتالوگ وجود ندارد.")
    deleted, _details = catalog.items.filter(lot_id=lot_id).delete()
    if not deleted:
        raise CatalogError("محصول در این کاتالوگ یافت نشد.")
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
    )
    CustomCatalog.objects.filter(pk=catalog.pk, first_viewed_at__isnull=True).update(first_viewed_at=now)
    catalog.refresh_from_db()
    return catalog


@transaction.atomic
def move_catalog_lot(
    *, catalog: CustomCatalog, membership: BusinessMembership, membership_id, direction: str
) -> bool:
    """Move one membership by one position with two bounded updates."""
    _require_catalog_manage(membership)
    if catalog.business_id != membership.business_id:
        raise CatalogError("دسترسی به این کاتالوگ وجود ندارد.")
    ordered = list(
        CustomCatalogItem.objects.select_for_update()
        .filter(catalog=catalog)
        .order_by("sort_order", "id")
    )
    moved = _move_ordered(ordered, membership_id, direction)
    if moved:
        catalog.save(update_fields=["updated_at"])
    return moved


@transaction.atomic
def regenerate_catalog_token(*, catalog: CustomCatalog, membership: BusinessMembership) -> str:
    """Permanently revoke the previous customer-catalog URL."""
    _require_catalog_manage(membership)
    if catalog.business_id != membership.business_id:
        raise CatalogError("دسترسی به این کاتالوگ وجود ندارد.")
    locked = CustomCatalog.objects.select_for_update().get(pk=catalog.pk)
    while True:
        token = secrets.token_urlsafe(16)
        if not CustomCatalog.objects.filter(share_token=token).exists():
            break
    locked.share_token = token
    locked.save(update_fields=["share_token", "updated_at"])
    catalog.share_token = token
    return token


@transaction.atomic
def duplicate_catalog(*, catalog: CustomCatalog, membership: BusinessMembership) -> CustomCatalog:
    """Copy editable content while resetting recipient, link and analytics."""
    _require_catalog_manage(membership)
    if catalog.business_id != membership.business_id:
        raise CatalogError("دسترسی به این کاتالوگ وجود ندارد.")
    duplicate = CustomCatalog.objects.create(
        business=catalog.business,
        title=f"نسخه مشابه {catalog.title}"[:200],
        customer_name="",
        custom_message=catalog.custom_message,
        expires_at=None,
        is_active=False,
    )
    CustomCatalogItem.objects.bulk_create(
        [
            CustomCatalogItem(
                catalog=duplicate,
                lot_id=item.lot_id,
                sort_order=item.sort_order,
                note=item.note,
            )
            for item in catalog.items.order_by("sort_order", "id")
        ]
    )
    return duplicate


DEFAULT_STOREFRONT_COLLECTIONS = (
    {
        "title": "انتخاب‌های اقتصادی",
        "description": "محصولات دارای قیمت روز، مرتب‌شده از قیمت کمتر",
        "suggestion_kind": StorefrontCollection.SuggestionKind.ECONOMIC,
    },
    {
        "title": "تازه‌های ویترین",
        "description": "محصولاتی که موجودی آن‌ها تازه‌تر تأیید شده است",
        "suggestion_kind": StorefrontCollection.SuggestionKind.FRESH,
    },
    {
        "title": "سنگ‌های مناسب نمای بیرونی",
        "description": "پیشنهاد بر پایه کاربرد ثبت‌شده برای هر محصول",
        "suggestion_kind": StorefrontCollection.SuggestionKind.EXTERIOR,
    },
    {
        "title": "پیشنهاد فروشنده",
        "description": "انتخاب‌های دست‌چین‌شده شما برای مشتریان",
        "suggestion_kind": StorefrontCollection.SuggestionKind.NONE,
    },
)


@transaction.atomic
def ensure_default_storefront_collections(*, business: Business) -> None:
    """Create editable, hidden starter sections only when none exist yet."""
    if StorefrontCollection.objects.filter(business=business).exists():
        return
    StorefrontCollection.objects.bulk_create(
        [
            StorefrontCollection(business=business, sort_order=index * 10, **definition)
            for index, definition in enumerate(DEFAULT_STOREFRONT_COLLECTIONS, start=1)
        ]
    )


def _require_collection_manage(collection: StorefrontCollection, membership: BusinessMembership) -> None:
    _require_catalog_manage(membership)
    if collection.business_id != membership.business_id:
        raise CatalogError("دسترسی به این مجموعه وجود ندارد.")


@transaction.atomic
def save_storefront_collection(
    *,
    business: Business,
    membership: BusinessMembership,
    title: str,
    description: str = "",
    is_active: bool = False,
    suggestion_kind: str = "",
    collection: StorefrontCollection | None = None,
    lot_ids: list | None = None,
) -> StorefrontCollection:
    _require_catalog_manage(membership)
    if membership.business_id != business.id:
        raise CatalogError("دسترسی نامعتبر است.")
    title = (title or "").strip()
    if len(title) < 2:
        raise CatalogError("عنوان مجموعه خیلی کوتاه است.")
    if collection is None:
        last = StorefrontCollection.objects.filter(business=business).order_by("-sort_order").first()
        collection = StorefrontCollection(business=business, sort_order=(last.sort_order + 10 if last else 10))
    elif collection.business_id != business.id:
        raise CatalogError("دسترسی به این مجموعه وجود ندارد.")
    collection.title = title
    collection.description = (description or "").strip()
    collection.is_active = bool(is_active)
    collection.suggestion_kind = suggestion_kind or StorefrontCollection.SuggestionKind.NONE
    try:
        collection.save()
    except IntegrityError as exc:
        if collection.business.storefront_collections.filter(title=title).exclude(pk=collection.pk).exists():
            raise CatalogError("مجموعه‌ای با این عنوان دارید.") from exc
        raise
    if lot_ids is not None:
        set_storefront_collection_lots(
            collection=collection,
            membership=membership,
            lot_ids=lot_ids,
        )
    return collection


@transaction.atomic
def set_storefront_collection_lots(
    *, collection: StorefrontCollection, membership: BusinessMembership, lot_ids: list
) -> StorefrontCollection:
    _require_collection_manage(collection, membership)
    requested = [value for value in lot_ids if value]
    try:
        owned = list(
            InventoryLot.objects.filter(
                business=collection.business,
                deleted_at__isnull=True,
                pk__in=requested,
            ).values_list("pk", flat=True)
        )
    except (DjangoValidationError, TypeError, ValueError) as exc:
        raise CatalogError("محصول انتخاب‌شده معتبر نیست.") from exc
    owned_map = {str(value): value for value in owned}
    unique_requested = list(dict.fromkeys(str(value) for value in requested))
    if len(owned_map) != len(unique_requested):
        raise CatalogError("یک یا چند محصول متعلق به کسب‌وکار شما نیست.")
    collection.items.all().delete()
    StorefrontCollectionItem.objects.bulk_create(
        [
            StorefrontCollectionItem(
                collection=collection,
                lot_id=owned_map[value],
                sort_order=index,
            )
            for index, value in enumerate(unique_requested)
        ]
    )
    collection.save(update_fields=["updated_at"])
    return collection


def suggested_storefront_lots(collection: StorefrontCollection, *, limit: int = 12) -> list[InventoryLot]:
    """Evidence-backed suggestions only; the seller can freely edit the result."""
    from .selectors import public_catalog_lots

    qs = public_catalog_lots(collection.business)
    if collection.suggestion_kind == StorefrontCollection.SuggestionKind.ECONOMIC:
        from apps.pricing.queries import effective_amount_subquery

        qs = qs.annotate(_b2c_amount=effective_amount_subquery("b2c")).filter(
            _b2c_amount__isnull=False
        ).order_by("_b2c_amount", "-stock_confirmed_at")
    elif collection.suggestion_kind == StorefrontCollection.SuggestionKind.FRESH:
        qs = qs.filter(stock_expires_at__gt=timezone.now()).order_by(
            F("stock_confirmed_at").desc(nulls_last=True), "-updated_at"
        )
    elif collection.suggestion_kind == StorefrontCollection.SuggestionKind.EXTERIOR:
        qs = qs.filter(product__applications__code="exterior-facade").order_by(
            F("stock_confirmed_at").desc(nulls_last=True), "-updated_at"
        )
    else:
        return []
    return list(qs[:limit])


@transaction.atomic
def apply_storefront_suggestions(
    *, collection: StorefrontCollection, membership: BusinessMembership
) -> StorefrontCollection:
    _require_collection_manage(collection, membership)
    suggested = suggested_storefront_lots(collection)
    current = list(collection.items.order_by("sort_order", "id").values_list("lot_id", flat=True))
    merged = current + [lot.pk for lot in suggested if lot.pk not in set(current)]
    return set_storefront_collection_lots(
        collection=collection,
        membership=membership,
        lot_ids=merged,
    )


@transaction.atomic
def move_storefront_collection(
    *, collection: StorefrontCollection, membership: BusinessMembership, direction: str
) -> None:
    _require_collection_manage(collection, membership)
    ordered = list(
        StorefrontCollection.objects.select_for_update().filter(business=collection.business).order_by(
            "sort_order", "created_at"
        )
    )
    _move_ordered(ordered, collection.pk, direction)


@transaction.atomic
def move_storefront_collection_item(
    *, membership_item: StorefrontCollectionItem, membership: BusinessMembership, direction: str
) -> None:
    _require_collection_manage(membership_item.collection, membership)
    ordered = list(
        StorefrontCollectionItem.objects.select_for_update().filter(
            collection=membership_item.collection
        ).order_by("sort_order", "id")
    )
    _move_ordered(ordered, membership_item.pk, direction)


def _move_ordered(ordered: list, target_id, direction: str) -> bool:
    index = next((idx for idx, value in enumerate(ordered) if value.pk == target_id), None)
    if index is None:
        return False
    other_index = index - 1 if direction == "up" else index + 1
    if other_index < 0 or other_index >= len(ordered):
        return False
    current = ordered[index]
    other = ordered[other_index]
    current.sort_order, other.sort_order = other.sort_order, current.sort_order
    current.save(update_fields=["sort_order"])
    other.save(update_fields=["sort_order"])
    return True


@transaction.atomic
def regenerate_storefront_token(*, business: Business, membership: BusinessMembership) -> str:
    _require_catalog_manage(membership)
    if membership.business_id != business.id:
        raise CatalogError("دسترسی نامعتبر است.")
    locked = Business.objects.select_for_update().get(pk=business.pk)
    while True:
        token = secrets.token_urlsafe(24)
        if not Business.objects.filter(storefront_token=token).exists():
            break
    locked.storefront_token = token
    locked.save(update_fields=["storefront_token", "updated_at"])
    business.storefront_token = token
    return token
