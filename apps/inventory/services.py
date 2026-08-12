from __future__ import annotations

import logging
import mimetypes
import uuid
from decimal import Decimal

from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.utils import timezone

from apps.businesses.entitlements import (
    CREATE_PRODUCTS,
    PUBLISH_PRODUCTS,
    EntitlementError,
    require_entitlement,
)
from apps.businesses.models import Business, BusinessMembership
from apps.businesses.permissions import (
    INVENTORY_CONFIRM,
    INVENTORY_CREATE,
    INVENTORY_EDIT,
    INVENTORY_MEDIA,
    INVENTORY_PUBLISH,
    INVENTORY_QUANTITY,
    PRICES_EDIT,
)
from apps.pricing.models import LotPrice
from apps.pricing.services import set_lot_price

from .models import Application, InventoryLot, LotMedia, Product

logger = logging.getLogger(__name__)

#: Upload ceilings. Generous enough for a phone photo or a short clip of a slab,
#: small enough that one bad request cannot fill the disk.
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_VIDEO_BYTES = 60 * 1024 * 1024

ALLOWED_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})
ALLOWED_VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".webm"})


class InventoryError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def _require(membership: BusinessMembership, capability: str) -> None:
    if membership is None or not membership.has_capability(capability):
        raise InventoryError("دسترسی لازم برای این عملیات را ندارید.")


def _require_plan(business: Business, entitlement: str) -> None:
    """Plan gate, enforced here rather than by hiding navigation.

    A browse-only Business that is stopped only by a missing menu item is not
    stopped at all — the form still posts.
    """
    try:
        require_entitlement(business, entitlement)
    except EntitlementError as exc:
        raise InventoryError(exc.message) from exc


def _require_owner(lot: InventoryLot, membership: BusinessMembership) -> None:
    if lot.business_id != membership.business_id:
        raise InventoryError("دسترسی به این محصول وجود ندارد.")


def _next_lot_code(business: Business) -> str:
    stamp = timezone.localtime().strftime("%y%m%d")
    suffix = uuid.uuid4().hex[:4].upper()
    return f"L-{stamp}-{suffix}"


@transaction.atomic
def create_or_get_product(
    *,
    business: Business,
    membership: BusinessMembership,
    commercial_name: str,
    stone_type: str = "",
    primary_color: str = "",
    quarry_region: str = "",
    product_id=None,
    applications: list[Application] | None = None,
) -> Product:
    _require(membership, INVENTORY_CREATE)
    _require_plan(business, CREATE_PRODUCTS)
    if product_id:
        product = Product.objects.filter(business=business, pk=product_id, is_active=True).first()
        if product is None:
            raise InventoryError("محصول یافت نشد.")
        if applications is not None:
            product.applications.set(applications)
        return product

    name = (commercial_name or "").strip()
    if len(name) < 2:
        raise InventoryError("نام محصول خیلی کوتاه است.")
    product = Product.objects.create(
        business=business,
        commercial_name=name,
        stone_type=(stone_type or "").strip(),
        primary_color=(primary_color or "").strip(),
        quarry_region=(quarry_region or "").strip(),
    )
    if applications:
        product.applications.set(applications)
    return product


@transaction.atomic
def create_draft_item(
    *,
    business: Business,
    membership: BusinessMembership,
    product: Product,
    lot_code: str = "",
    grade: str = "",
    processing_type: str = "",
    description: str = "",
    location_province: str = "",
    location_city: str = "",
    location_address: str = "",
    stock_mode: str = InventoryLot.StockMode.EXACT,
    available_sqm: Decimal | None = None,
    stock_valid_for_days: int = 7,
    length_cm: Decimal | None = None,
    width_cm: Decimal | None = None,
    thickness_mm: Decimal | None = None,
    slab_count: int | None = None,
) -> InventoryLot:
    _require(membership, INVENTORY_CREATE)
    _require_plan(business, CREATE_PRODUCTS)
    if product.business_id != business.id:
        raise InventoryError("محصول متعلق به این کسب‌وکار نیست.")

    code = (lot_code or "").strip() or _next_lot_code(business)
    if InventoryLot.objects.filter(business=business, lot_code=code).exists():
        raise InventoryError("کد محصول تکراری است.")

    if stock_mode not in set(InventoryLot.StockMode.values):
        raise InventoryError("نوع موجودی نامعتبر است.")

    qty = available_sqm if available_sqm is not None else Decimal("0")
    if qty < 0:
        raise InventoryError("مقدار موجودی نمی‌تواند منفی باشد.")

    lot = InventoryLot.objects.create(
        business=business,
        product=product,
        lot_code=code,
        status=InventoryLot.Status.DRAFT,
        is_visible=False,
        availability_status=InventoryLot.Availability.AVAILABLE,
        stock_mode=stock_mode,
        available_sqm=qty,
        original_sqm=qty,
        # Entering a quantity is itself a confirmation of it.
        stock_confirmed_at=timezone.now(),
        stock_valid_for_days=stock_valid_for_days,
        location_province=(location_province or business.province or "").strip(),
        location_city=(location_city or business.city or "").strip(),
        location_address=(location_address or "").strip(),
        grade=(grade or "").strip(),
        processing_type=(processing_type or "").strip(),
        description=(description or "").strip(),
        length_cm=length_cm,
        width_cm=width_cm,
        thickness_mm=thickness_mm,
        slab_count=slab_count,
    )
    logger.info("Draft item created business=%s item=%s", business.id, lot.id)
    return lot


@transaction.atomic
def update_item(
    *,
    lot: InventoryLot,
    membership: BusinessMembership,
    fields: dict | None = None,
    b2b_price: dict | None = None,
    b2c_price: dict | None = None,
) -> InventoryLot:
    """Apply one user-visible edit as one transaction.

    Previously the edit screen called three services in a row, each with its own
    transaction, so a rejected price left the item's other fields already
    changed. A service boundary should match what the user thinks of as a single
    action, which here is "save this product".
    """
    _require(membership, INVENTORY_EDIT)
    _require_owner(lot, membership)

    fields = fields or {}

    quantity_keys = {"available_sqm", "original_sqm", "slab_count", "stock_mode", "stock_valid_for_days"}
    if quantity_keys.intersection(fields) and not membership.has_capability(INVENTORY_QUANTITY):
        raise InventoryError("اجازه تغییر مقدار موجودی را ندارید.")

    publish_keys = {"is_visible", "availability_status"}
    changing_publish = any(key in fields and getattr(lot, key) != fields[key] for key in publish_keys)
    if changing_publish and not membership.has_capability(INVENTORY_PUBLISH):
        raise InventoryError("اجازه تغییر وضعیت انتشار را ندارید.")
    if fields.get("is_visible") and not lot.is_visible:
        _require_plan(lot.business, PUBLISH_PRODUCTS)

    allowed = {
        "grade",
        "processing_type",
        "description",
        "defect_notes",
        "stock_mode",
        "available_sqm",
        "stock_valid_for_days",
        "original_sqm",
        "length_cm",
        "width_cm",
        "thickness_mm",
        "slab_count",
        "bundle_count",
        "min_sale_qty",
        "location_province",
        "location_city",
        "location_address",
        "ready_for_loading_at",
        "photographed_at",
        "is_featured",
        "is_urgent_sale",
        "is_visible",
        "availability_status",
    }
    stock_changed = False
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key in {"available_sqm", "stock_mode"} and getattr(lot, key) != value:
            stock_changed = True
        setattr(lot, key, value)

    # Changing the number restarts the window: the seller has just told us what
    # it is now.
    if stock_changed:
        lot.stock_confirmed_at = timezone.now()

    lot.save()

    if b2b_price is not None:
        _set_price(lot=lot, membership=membership, tier_code="b2b", spec=b2b_price)
    if b2c_price is not None:
        _set_price(lot=lot, membership=membership, tier_code="b2c", spec=b2c_price)

    return lot


def _set_price(*, lot: InventoryLot, membership: BusinessMembership, tier_code: str, spec: dict) -> None:
    if not membership.has_capability(PRICES_EDIT):
        raise InventoryError("اجازه تغییر قیمت را ندارید.")
    try:
        set_lot_price(
            lot=lot,
            tier_code=tier_code,
            amount=spec.get("amount"),
            mode=spec.get("mode"),
            currency=spec.get("currency") or "IRR",
            unit=spec.get("unit") or LotPrice.Unit.PER_SQM,
            valid_for_days=spec.get("valid_for_days"),
            special_amount=spec.get("special_amount"),
            special_until=spec.get("special_until"),
        )
    except ValueError as exc:
        raise InventoryError(str(exc)) from exc


@transaction.atomic
def set_item_visibility(*, lot: InventoryLot, membership: BusinessMembership, is_visible: bool) -> InventoryLot:
    _require(membership, INVENTORY_PUBLISH)
    _require_owner(lot, membership)
    if is_visible:
        _require_plan(lot.business, PUBLISH_PRODUCTS)
    lot.is_visible = bool(is_visible)
    if lot.is_visible:
        lot.status = InventoryLot.Status.ACTIVE
    lot.save(update_fields=["is_visible", "status", "updated_at"])
    return lot


@transaction.atomic
def publish_item(*, lot: InventoryLot, membership: BusinessMembership, is_visible: bool = True) -> InventoryLot:
    """Take an item out of draft, optionally publishing it at the same time."""
    _require(membership, INVENTORY_PUBLISH)
    _require_owner(lot, membership)
    if is_visible:
        _require_plan(lot.business, PUBLISH_PRODUCTS)
    lot.status = InventoryLot.Status.ACTIVE
    lot.is_visible = bool(is_visible)
    if lot.stock_confirmed_at is None:
        lot.stock_confirmed_at = timezone.now()
    lot.save()
    return lot


@transaction.atomic
def confirm_item_stock(
    *,
    lot: InventoryLot,
    membership: BusinessMembership,
    available_sqm: Decimal | None = None,
    stock_mode: str | None = None,
) -> InventoryLot:
    """Restart the stock validity window, optionally with a new quantity.

    This is the action behind «تأیید موجودی» and the seller's reply to a stock
    inquiry. It never touches availability: a seller who has actually run out
    should mark the item ناموجود instead.
    """
    _require(membership, INVENTORY_CONFIRM)
    _require_owner(lot, membership)

    if stock_mode is not None:
        if stock_mode not in set(InventoryLot.StockMode.values):
            raise InventoryError("نوع موجودی نامعتبر است.")
        lot.stock_mode = stock_mode
    if available_sqm is not None:
        if available_sqm < 0:
            raise InventoryError("مقدار موجودی نمی‌تواند منفی باشد.")
        lot.available_sqm = available_sqm

    lot.stock_confirmed_at = timezone.now()
    lot.save()
    return lot


@transaction.atomic
def set_item_availability(*, lot: InventoryLot, membership: BusinessMembership, available: bool) -> InventoryLot:
    """Mark «موجود» / «ناموجود».

    Unavailable removes the item from every buyer-facing surface at once, via
    :func:`apps.inventory.policy.eligible_items`, without deleting anything: the
    seller flips it back later without re-creating the product.
    """
    _require(membership, INVENTORY_EDIT)
    _require_owner(lot, membership)
    lot.availability_status = (
        InventoryLot.Availability.AVAILABLE if available else InventoryLot.Availability.UNAVAILABLE
    )
    lot.save(update_fields=["availability_status", "updated_at"])
    logger.info("Item availability item=%s available=%s", lot.id, available)
    return lot


def item_has_commercial_history(lot: InventoryLot) -> bool:
    """True when deleting the row would break a historical record.

    Checked against live relations rather than a stored flag, so a newly created
    invoice immediately protects the item it references.
    """
    if lot.inquiries.exists():
        return True
    if lot.ledger_entries.exists():
        return True
    for accessor in ("purchase_requests", "trades", "invoice_items"):
        related = getattr(lot, accessor, None)
        if related is not None and related.exists():
            return True
    return False


@transaction.atomic
def delete_item(*, lot: InventoryLot, membership: BusinessMembership) -> str:
    """Delete an item, preserving anything history depends on.

    Returns ``"purged"`` or ``"archived"`` so the caller can word the message
    honestly. The seller sees the same outcome either way — the product is gone
    from every list they manage and every surface a buyer can reach — but an
    item that appears on an invoice keeps its row so that invoice stays intact.
    """
    _require(membership, INVENTORY_EDIT)
    _require_owner(lot, membership)

    # Manual catalog membership is not history: drop it in both branches so an
    # old curated link cannot resurrect the product.
    lot.custom_catalog_items.all().delete()

    if item_has_commercial_history(lot):
        lot.deleted_at = timezone.now()
        lot.is_visible = False
        lot.availability_status = InventoryLot.Availability.UNAVAILABLE
        lot.save(update_fields=["deleted_at", "is_visible", "availability_status", "updated_at"])
        logger.info("Item archived instead of purged (has history) item=%s", lot.id)
        return "archived"

    item_id = lot.id
    lot.prices.all().delete()
    lot.media.all().delete()
    lot.delete()
    logger.info("Item purged item=%s", item_id)
    return "purged"


def _classify_upload(upload: UploadedFile) -> str:
    """Decide image vs video, and refuse anything else.

    The browser-supplied content type is treated as a hint only; the extension
    has to agree. A caller can set any Content-Type header they like, so trusting
    it alone would let an arbitrary file through under an image label.
    """
    name = (upload.name or "").lower()
    extension = name[name.rfind(".") :] if "." in name else ""
    declared = (getattr(upload, "content_type", "") or "").lower()
    guessed = (mimetypes.guess_type(name)[0] or "").lower()

    if extension in ALLOWED_IMAGE_EXTENSIONS and (
        not declared or declared.startswith("image/") or guessed.startswith("image/")
    ):
        return LotMedia.Kind.IMAGE
    if extension in ALLOWED_VIDEO_EXTENSIONS and (
        not declared or declared.startswith("video/") or guessed.startswith("video/")
    ):
        return LotMedia.Kind.VIDEO
    raise InventoryError("فقط تصویر (jpg, png, webp, gif) یا ویدیو (mp4, mov, webm) قابل بارگذاری است.")


@transaction.atomic
def add_lot_media(
    *,
    lot: InventoryLot,
    membership: BusinessMembership,
    upload: UploadedFile,
    is_primary: bool = False,
    caption: str = "",
) -> LotMedia:
    _require(membership, INVENTORY_MEDIA)
    _require_owner(lot, membership)

    kind = _classify_upload(upload)
    limit = MAX_IMAGE_BYTES if kind == LotMedia.Kind.IMAGE else MAX_VIDEO_BYTES
    if (upload.size or 0) > limit:
        raise InventoryError(f"حجم فایل بیش از حد مجاز است (حداکثر {limit // (1024 * 1024)} مگابایت).")

    if is_primary or not lot.media.filter(is_primary=True).exists():
        lot.media.filter(is_primary=True).update(is_primary=False)
        is_primary = True

    media = LotMedia.objects.create(
        lot=lot,
        kind=kind,
        file=upload,
        caption=(caption or "").strip(),
        sort_order=lot.media.count(),
        is_primary=is_primary,
    )
    if lot.photographed_at is None and kind == LotMedia.Kind.IMAGE:
        lot.photographed_at = timezone.localdate()
        lot.save(update_fields=["photographed_at", "updated_at"])
    return media


@transaction.atomic
def delete_lot_media(*, lot: InventoryLot, membership: BusinessMembership, media_id) -> None:
    _require(membership, INVENTORY_MEDIA)
    _require_owner(lot, membership)

    media = lot.media.filter(pk=media_id).first()
    if media is None:
        raise InventoryError("فایل یافت نشد.")
    was_primary = media.is_primary
    media.delete()
    if was_primary:
        # Never leave a gallery with no cover: promote whatever is now first.
        replacement = lot.media.order_by("sort_order", "created_at").first()
        if replacement is not None:
            replacement.is_primary = True
            replacement.save(update_fields=["is_primary"])


@transaction.atomic
def set_primary_media(*, lot: InventoryLot, membership: BusinessMembership, media_id) -> None:
    _require(membership, INVENTORY_MEDIA)
    _require_owner(lot, membership)

    media = lot.media.filter(pk=media_id).first()
    if media is None:
        raise InventoryError("فایل یافت نشد.")
    if media.kind != LotMedia.Kind.IMAGE:
        raise InventoryError("فقط تصویر می‌تواند تصویر اصلی باشد.")
    lot.media.filter(is_primary=True).update(is_primary=False)
    media.is_primary = True
    media.save(update_fields=["is_primary"])


@transaction.atomic
def reorder_lot_media(*, lot: InventoryLot, membership: BusinessMembership, media_ids: list) -> None:
    """Apply a new gallery order.

    Ids that do not belong to this item are ignored rather than rejected: a
    reorder is a cosmetic action, and a stale tab submitting a removed id should
    not produce an error the seller cannot act on.
    """
    _require(membership, INVENTORY_MEDIA)
    _require_owner(lot, membership)

    owned = {str(pk): pk for pk in lot.media.values_list("pk", flat=True)}
    ordered = [owned[str(mid)] for mid in media_ids if str(mid) in owned]
    for position, pk in enumerate(ordered):
        LotMedia.objects.filter(pk=pk).update(sort_order=position)


@transaction.atomic
def duplicate_item(*, lot: InventoryLot, membership: BusinessMembership) -> InventoryLot:
    _require(membership, INVENTORY_CREATE)
    _require_plan(lot.business, CREATE_PRODUCTS)
    _require_owner(lot, membership)

    clone = InventoryLot.objects.create(
        business=lot.business,
        product=lot.product,
        lot_code=_next_lot_code(lot.business),
        status=InventoryLot.Status.DRAFT,
        is_visible=False,
        availability_status=InventoryLot.Availability.AVAILABLE,
        stock_mode=lot.stock_mode,
        available_sqm=lot.available_sqm,
        original_sqm=lot.original_sqm,
        stock_confirmed_at=timezone.now(),
        stock_valid_for_days=lot.stock_valid_for_days,
        location_province=lot.location_province,
        location_city=lot.location_city,
        location_address=lot.location_address,
        slab_count=lot.slab_count,
        bundle_count=lot.bundle_count,
        length_cm=lot.length_cm,
        width_cm=lot.width_cm,
        thickness_mm=lot.thickness_mm,
        grade=lot.grade,
        processing_type=lot.processing_type,
        min_sale_qty=lot.min_sale_qty,
        description=lot.description,
        defect_notes=lot.defect_notes,
        is_featured=False,
        is_urgent_sale=False,
    )
    for price in lot.prices.select_related("tier"):
        set_lot_price(
            lot=clone,
            tier_code=price.tier.code,
            amount=price.amount,
            mode=price.mode,
            currency=price.currency,
            unit=price.unit,
            valid_for_days=price.price_valid_for_days,
        )
    return clone
