from __future__ import annotations

import logging
import secrets
from decimal import Decimal

from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.utils import timezone

from apps.businesses.eligibility import NotOperationalError, require_operational
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
from apps.pricing.services import set_lot_price

from .media_validation import MediaValidationError, verify_image, verify_video
from .models import Application, InventoryLot, LotMedia, Product, VocabularyTerm
from .taxonomy import normalize_searchable

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
    _require_operational(membership.business)


def _require_operational(business: Business) -> None:
    """A suspended or expired tenant may read its own records but not change them.

    The plan gate already blocked creating and publishing, because those consult
    entitlements and a non-operational Business has none. Editing, re-pricing,
    confirming stock, uploading media and deleting consulted only the member's
    capability, so a suspended Business could keep working on everything it
    already had.
    """
    try:
        require_operational(business)
    except NotOperationalError as exc:
        raise InventoryError(exc.message) from exc


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


_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _next_lot_code(stone: VocabularyTerm) -> str:
    prefix = (stone.code_prefix or "S").upper()
    for _attempt in range(20):
        suffix = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))
        candidate = f"{prefix}-{suffix}"
        if not InventoryLot.objects.filter(lot_code=candidate).exists():
            return candidate
    raise InventoryError("ساخت کد یکتا ممکن نشد؛ دوباره تلاش کنید.")


@transaction.atomic
def create_product(
    *,
    business: Business,
    membership: BusinessMembership,
    stone: VocabularyTerm,
    name_suffix: str = "",
    pattern: str = "",
    description_public: str = "",
    description_professional: str = "",
    applications: list[Application] | None = None,
) -> Product:
    _require(membership, INVENTORY_CREATE)
    _require_plan(business, CREATE_PRODUCTS)
    if stone.kind != VocabularyTerm.Kind.STONE_TYPE or not stone.is_active:
        raise InventoryError("نوع سنگ انتخاب‌شده معتبر نیست.")
    product = Product.objects.create(
        business=business,
        stone=stone,
        name_suffix=normalize_searchable(name_suffix),
        pattern=normalize_searchable(pattern),
        description_public=(description_public or "").strip(),
        description_professional=(description_professional or "").strip(),
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
    processing_type: str = "",
    description: str = "",
    available_sqm: Decimal | None = None,
    stock_valid_for_days: int = 7,
    length_cm: Decimal | None = None,
    width_cm: Decimal | None = None,
    thickness_mm: Decimal | None = None,
    min_sale_qty: Decimal | None = None,
    defect_notes: str = "",
    availability_status: str = InventoryLot.Availability.AVAILABLE,
    is_visible: bool = False,
    is_urgent_sale: bool = False,
    b2b_price: dict | None = None,
    b2c_price: dict | None = None,
) -> InventoryLot:
    """Create the sellable item, with its prices, as one action.

    Prices belong here rather than in a second service call. The wizard used to
    create the draft and then set the prices in two transactions, so a member
    without ``prices.edit`` — which the default staff role does not have — got a
    saved but unpriced draft plus an error, and had to find and clean it up
    themselves. A service boundary should match what the user thinks of as one
    action, which here is "add this product".
    """
    _require(membership, INVENTORY_CREATE)
    _require_plan(business, CREATE_PRODUCTS)
    if product.business_id != business.id:
        raise InventoryError("محصول متعلق به این کسب‌وکار نیست.")

    if hasattr(product, "lot"):
        raise InventoryError("برای این محصول قبلاً یک آیتم موجودی ساخته شده است.")
    if available_sqm is not None and available_sqm < 0:
        raise InventoryError("مقدار موجودی نمی‌تواند منفی باشد.")
    try:
        stock_valid_for_days = int(stock_valid_for_days)
    except (TypeError, ValueError) as exc:
        raise InventoryError("مدت اعتبار موجودی معتبر نیست.") from exc
    if not 1 <= stock_valid_for_days <= 365:
        raise InventoryError("مدت اعتبار موجودی باید بین ۱ تا ۳۶۵ روز باشد.")
    if availability_status not in InventoryLot.Availability.values:
        raise InventoryError("وضعیت موجود بودن نامعتبر است.")
    if is_visible:
        if not membership.has_capability(INVENTORY_PUBLISH):
            raise InventoryError("اجازه انتشار محصول را ندارید.")
        _require_plan(business, PUBLISH_PRODUCTS)

    lot = InventoryLot.objects.create(
        business=business,
        product=product,
        lot_code=_next_lot_code(product.stone),
        status=InventoryLot.Status.ACTIVE if is_visible else InventoryLot.Status.DRAFT,
        is_visible=is_visible,
        availability_status=availability_status,
        available_sqm=available_sqm,
        stock_confirmed_at=timezone.now() if available_sqm is not None else None,
        stock_valid_for_days=stock_valid_for_days,
        processing_type=normalize_searchable(processing_type) or "ساب خورده",
        description=(description or "").strip(),
        length_cm=length_cm,
        width_cm=width_cm,
        thickness_mm=thickness_mm,
        min_sale_qty=min_sale_qty or Decimal("0"),
        defect_notes=(defect_notes or "").strip(),
        is_urgent_sale=is_urgent_sale,
    )

    if b2b_price is not None:
        _set_price(lot=lot, membership=membership, tier_code="b2b", spec=b2b_price)
    if b2c_price is not None:
        _set_price(lot=lot, membership=membership, tier_code="b2c", spec=b2c_price)

    logger.info("Draft item created business=%s item=%s", business.id, lot.id)
    return lot


@transaction.atomic
def create_product_item(
    *,
    business: Business,
    membership: BusinessMembership,
    product_fields: dict,
    item_fields: dict,
    applications: list[Application] | None = None,
    b2b_price: dict | None = None,
    b2c_price: dict | None = None,
) -> InventoryLot:
    """Create the product, its sole inventory item, and both prices atomically."""

    product = create_product(
        business=business,
        membership=membership,
        applications=applications,
        **product_fields,
    )
    return create_draft_item(
        business=business,
        membership=membership,
        product=product,
        b2b_price=b2b_price,
        b2c_price=b2c_price,
        **item_fields,
    )


@transaction.atomic
def update_product_item(
    *,
    lot: InventoryLot,
    membership: BusinessMembership,
    product_fields: dict,
    item_fields: dict,
    applications: list[Application] | None = None,
    b2b_price: dict | None = None,
    b2c_price: dict | None = None,
) -> InventoryLot:
    """Update the product and its one sellable item as one user action."""

    _require(membership, INVENTORY_EDIT)
    _require_owner(lot, membership)
    product = lot.product
    stone = product_fields.get("stone", product.stone)
    if stone.kind != VocabularyTerm.Kind.STONE_TYPE or not stone.is_active:
        raise InventoryError("نوع سنگ انتخاب‌شده معتبر نیست.")
    for key in ("stone", "name_suffix", "pattern", "description_public", "description_professional"):
        if key in product_fields:
            value = product_fields[key]
            if key in {"name_suffix", "pattern"}:
                value = normalize_searchable(value)
            setattr(product, key, value)
    product.save()
    if applications is not None:
        product.applications.set(applications)
    return update_item(
        lot=lot,
        membership=membership,
        fields=item_fields,
        b2b_price=b2b_price,
        b2c_price=b2c_price,
    )


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

    quantity_keys = {"available_sqm", "stock_valid_for_days"}
    changing_quantity = any(
        key in fields and getattr(lot, key) != fields[key] for key in quantity_keys
    )
    if changing_quantity and not membership.has_capability(INVENTORY_QUANTITY):
        raise InventoryError("اجازه تغییر مقدار موجودی را ندارید.")

    publish_keys = {"is_visible", "availability_status"}
    changing_publish = any(key in fields and getattr(lot, key) != fields[key] for key in publish_keys)
    if changing_publish and not membership.has_capability(INVENTORY_PUBLISH):
        raise InventoryError("اجازه تغییر وضعیت انتشار را ندارید.")
    if fields.get("is_visible") and not lot.is_visible:
        _require_plan(lot.business, PUBLISH_PRODUCTS)

    allowed = {
        "processing_type",
        "description",
        "defect_notes",
        "available_sqm",
        "stock_valid_for_days",
        "length_cm",
        "width_cm",
        "thickness_mm",
        "min_sale_qty",
        "ready_for_loading_at",
        "photographed_at",
        "is_featured",
        "is_urgent_sale",
        "is_visible",
        "availability_status",
    }
    #: Searchable text goes through the same normalization on edit as on create.
    #: Applying it only at creation would let an edit reintroduce exactly the
    #: unsearchable spelling the create path exists to prevent.
    normalizers = {
        "processing_type": normalize_searchable,
    }

    stock_changed = False
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key == "available_sqm" and getattr(lot, key) != value:
            stock_changed = True
        normalize = normalizers.get(key)
        setattr(lot, key, normalize(value) if normalize and isinstance(value, str) else value)

    # Changing the number restarts the window: the seller has just told us what
    # it is now.
    if stock_changed:
        lot.stock_confirmed_at = timezone.now() if lot.available_sqm is not None else None
    if lot.is_visible:
        lot.status = InventoryLot.Status.ACTIVE

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
    if lot.available_sqm is not None and lot.stock_confirmed_at is None:
        lot.stock_confirmed_at = timezone.now()
    lot.save()
    return lot


@transaction.atomic
def confirm_item_stock(
    *,
    lot: InventoryLot,
    membership: BusinessMembership,
    available_sqm: Decimal | None = None,
    stock_valid_for_days: int | None = None,
) -> InventoryLot:
    """Restart the stock validity window, optionally with a new quantity.

    This is the action behind «تأیید موجودی» and the seller's reply to a stock
    inquiry. It never touches availability: a seller who has actually run out
    should mark the item ناموجود instead.
    """
    _require(membership, INVENTORY_CONFIRM)
    _require_owner(lot, membership)

    if available_sqm is not None and available_sqm < 0:
        raise InventoryError("مقدار موجودی نمی‌تواند منفی باشد.")
    lot.available_sqm = available_sqm
    if stock_valid_for_days is not None:
        # The confirmation screen has always asked how long the number should be
        # trusted for; the view simply never passed the answer on, so the seller
        # was told the change had been saved while the old window stayed in force.
        if not 1 <= int(stock_valid_for_days) <= 365:
            raise InventoryError("مدت اعتبار موجودی باید بین ۱ تا ۳۶۵ روز باشد.")
        lot.stock_valid_for_days = int(stock_valid_for_days)

    lot.stock_confirmed_at = timezone.now() if available_sqm is not None else None
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
    media = list(lot.media.all())
    lot.prices.all().delete()
    lot.media.all().delete()
    lot.delete()
    schedule_storage_cleanup(*media)
    logger.info("Item purged item=%s", item_id)
    return "purged"


UNSUPPORTED_UPLOAD = "فقط تصویر (jpg, png, webp, gif) یا ویدیو (mp4, mov, webm) قابل بارگذاری است."


def _claimed_kind(upload: UploadedFile) -> str:
    """Which check to run, decided by the extension alone.

    Only used to pick a size limit and a validator. It settles nothing: the
    extension is caller-supplied, and so are Content-Type and
    ``mimetypes.guess_type`` — which is itself derived from the extension. All
    three agreed with each other about a file that was not an image at all.
    """
    name = (upload.name or "").lower()
    extension = name[name.rfind(".") :] if "." in name else ""
    if extension in ALLOWED_IMAGE_EXTENSIONS:
        return LotMedia.Kind.IMAGE
    if extension in ALLOWED_VIDEO_EXTENSIONS:
        return LotMedia.Kind.VIDEO
    raise InventoryError(UNSUPPORTED_UPLOAD)


def _classify_upload(upload: UploadedFile) -> str:
    """Prove the upload is what its name claims, by reading it.

    Renaming a script to ``stone.jpg`` and posting it as ``image/jpeg`` used to
    pass every check there was. Images are decoded; videos are matched against
    their container signature.
    """
    kind = _claimed_kind(upload)
    try:
        if kind == LotMedia.Kind.IMAGE:
            verify_image(upload)
        else:
            verify_video(upload)
    except MediaValidationError as exc:
        raise InventoryError(exc.message) from exc
    return kind


def schedule_storage_cleanup(*media: LotMedia) -> None:
    """Delete the underlying objects once the transaction commits.

    Django does not remove a ``FileField``'s object when the row goes, so every
    deleted photo and video used to stay in storage — paid for, and still
    reachable at its old URL on a bucket served directly.

    On commit rather than inline, because the opposite failure is worse: a
    rolled-back transaction that had already deleted the file leaves a row
    pointing at nothing. Failures are logged and swallowed; a missing object is
    the desired end state, and re-raising here would fail a request whose
    database work has already been committed.
    """
    names = [name for item in media for name in (item.file.name, getattr(item.thumbnail, "name", None)) if name]
    if not names:
        return

    storage = media[0].file.storage

    def _delete() -> None:
        for name in names:
            try:
                storage.delete(name)
            except Exception:  # noqa: BLE001 - cleanup must not break the response
                logger.warning("Could not delete stored media object %s", name, exc_info=True)

    transaction.on_commit(_delete)


def _lock_lot(lot: InventoryLot) -> InventoryLot:
    """Serialize primary-image changes for one item.

    Two uploads, or two "make this the cover" clicks, arriving together both
    demoted the current primary and both promoted their own, and the gallery
    ended up with two covers. The row lock makes them queue; the partial unique
    index on the model is what holds if anything reaches the database another
    way.
    """
    return InventoryLot.objects.select_for_update().get(pk=lot.pk)


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

    # Size first, so an oversized upload is refused without being decoded. The
    # limit comes from the claimed kind; the content check below is what settles
    # whether that claim was true.
    limit = MAX_IMAGE_BYTES if _claimed_kind(upload) == LotMedia.Kind.IMAGE else MAX_VIDEO_BYTES
    if (upload.size or 0) > limit:
        raise InventoryError(f"حجم فایل بیش از حد مجاز است (حداکثر {limit // (1024 * 1024)} مگابایت).")

    kind = _classify_upload(upload)

    # Held for the rest of the transaction, so two concurrent uploads cannot both
    # decide they are the first and both become the cover.
    _lock_lot(lot)

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
    _lock_lot(lot)

    media = lot.media.filter(pk=media_id).first()
    if media is None:
        raise InventoryError("فایل یافت نشد.")
    was_primary = media.is_primary
    media.delete()
    schedule_storage_cleanup(media)
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
    _lock_lot(lot)

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

    product = Product.objects.create(
        business=lot.business,
        stone=lot.product.stone,
        name_suffix=lot.product.name_suffix,
        pattern=lot.product.pattern,
        vein_notes=lot.product.vein_notes,
        technical_notes=lot.product.technical_notes,
        description_public=lot.product.description_public,
        description_professional=lot.product.description_professional,
        alt_names=lot.product.alt_names,
    )
    product.applications.set(lot.product.applications.all())
    clone = InventoryLot.objects.create(
        business=lot.business,
        product=product,
        lot_code=_next_lot_code(product.stone),
        status=InventoryLot.Status.DRAFT,
        is_visible=False,
        availability_status=InventoryLot.Availability.AVAILABLE,
        available_sqm=lot.available_sqm,
        stock_confirmed_at=timezone.now() if lot.available_sqm is not None else None,
        stock_valid_for_days=lot.stock_valid_for_days,
        length_cm=lot.length_cm,
        width_cm=lot.width_cm,
        thickness_mm=lot.thickness_mm,
        processing_type=lot.processing_type,
        min_sale_qty=lot.min_sale_qty,
        description=lot.description,
        defect_notes=lot.defect_notes,
        is_featured=False,
        is_urgent_sale=False,
    )
    # Standard prices are copied; the special sale deliberately is not.
    #
    # A promotion is a decision about one batch at one moment — «تا آخر هفته» on
    # the stone in the yard now — and the copy is a different batch the seller is
    # about to change. Carrying it over silently would have the new item quoting a
    # discount nobody chose, sometimes with an end date already in the past. The
    # duplicate screen says so, so the reset is visible rather than a field that
    # went quiet.
    for price in lot.prices.select_related("tier"):
        set_lot_price(
            lot=clone,
            tier_code=price.tier.code,
            amount=price.amount,
            mode=price.mode,
            currency=price.currency,
            valid_for_days=price.price_valid_for_days,
        )
    return clone
