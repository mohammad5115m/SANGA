from __future__ import annotations

import logging
import uuid
from decimal import Decimal

from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.utils import timezone

from apps.businesses.models import Business, BusinessMembership, Warehouse
from apps.businesses.permissions import (
    INVENTORY_CONFIRM,
    INVENTORY_CREATE,
    INVENTORY_EDIT,
    INVENTORY_MEDIA,
    INVENTORY_PUBLISH,
    INVENTORY_QUANTITY,
    PRICES_EDIT,
)
from apps.pricing.services import set_lot_prices

from .freshness import evaluate_freshness
from .models import InventoryLot, LotMedia, Product

logger = logging.getLogger(__name__)


class InventoryError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def _require(membership: BusinessMembership, capability: str) -> None:
    if membership is None or not membership.has_capability(capability):
        raise InventoryError("دسترسی لازم برای این عملیات را ندارید.")


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
) -> Product:
    _require(membership, INVENTORY_CREATE)
    if product_id:
        product = Product.objects.filter(business=business, pk=product_id, is_active=True).first()
        if product is None:
            raise InventoryError("محصول یافت نشد.")
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
    return product


@transaction.atomic
def create_draft_lot(
    *,
    business: Business,
    membership: BusinessMembership,
    product: Product,
    warehouse: Warehouse,
    lot_code: str = "",
    grade: str = "",
    processing_type: str = "",
    description: str = "",
    available_sqm: Decimal = Decimal("0"),
    original_sqm: Decimal | None = None,
    length_cm: Decimal | None = None,
    width_cm: Decimal | None = None,
    thickness_mm: Decimal | None = None,
    slab_count: int | None = None,
) -> InventoryLot:
    _require(membership, INVENTORY_CREATE)
    if product.business_id != business.id:
        raise InventoryError("محصول متعلق به این کسب‌وکار نیست.")
    if warehouse.business_id != business.id:
        raise InventoryError("انبار متعلق به این کسب‌وکار نیست.")

    code = (lot_code or "").strip() or _next_lot_code(business)
    if InventoryLot.objects.filter(business=business, lot_code=code).exists():
        raise InventoryError("کد محموله تکراری است.")

    qty = available_sqm if available_sqm is not None else Decimal("0")
    if qty < 0:
        raise InventoryError("مقدار موجودی نمی‌تواند منفی باشد.")

    lot = InventoryLot.objects.create(
        business=business,
        product=product,
        warehouse=warehouse,
        lot_code=code,
        status=InventoryLot.Status.DRAFT,
        visibility=InventoryLot.Visibility.PRIVATE,
        available_sqm=qty,
        original_sqm=original_sqm if original_sqm is not None else qty,
        grade=(grade or "").strip(),
        processing_type=(processing_type or "").strip(),
        description=(description or "").strip(),
        length_cm=length_cm,
        width_cm=width_cm,
        thickness_mm=thickness_mm,
        slab_count=slab_count,
    )
    logger.info("Draft lot created business=%s lot=%s", business.id, lot.id)
    return lot


@transaction.atomic
def update_lot_fields(
    *,
    lot: InventoryLot,
    membership: BusinessMembership,
    **fields,
) -> InventoryLot:
    _require(membership, INVENTORY_EDIT)
    if lot.business_id != membership.business_id:
        raise InventoryError("دسترسی به این محموله وجود ندارد.")

    quantity_keys = {"available_sqm", "original_sqm", "slab_count", "bundle_count"}
    if quantity_keys.intersection(fields) and not membership.has_capability(INVENTORY_QUANTITY):
        raise InventoryError("اجازه تغییر مقدار موجودی را ندارید.")

    allowed = {
        "grade",
        "processing_type",
        "description",
        "defect_notes",
        "available_sqm",
        "original_sqm",
        "length_cm",
        "width_cm",
        "thickness_mm",
        "slab_count",
        "bundle_count",
        "min_sale_qty",
        "ready_for_loading_at",
        "photographed_at",
        "offer_expires_at",
        "is_featured",
        "is_urgent_sale",
        "warehouse",
    }
    for key, value in fields.items():
        if key in allowed:
            setattr(lot, key, value)
    lot.save()
    return lot


@transaction.atomic
def set_visibility_and_status(
    *,
    lot: InventoryLot,
    membership: BusinessMembership,
    visibility: str,
    publish: bool = False,
    save_as_draft: bool = False,
) -> InventoryLot:
    if publish or visibility != lot.visibility:
        _require(membership, INVENTORY_PUBLISH)
    if lot.business_id != membership.business_id:
        raise InventoryError("دسترسی به این محموله وجود ندارد.")

    if visibility not in InventoryLot.Visibility.values:
        raise InventoryError("وضعیت نمایش نامعتبر است.")
    lot.visibility = visibility

    if save_as_draft:
        lot.status = InventoryLot.Status.DRAFT
    elif publish:
        lot.status = InventoryLot.Status.AVAILABLE
        if lot.inventory_confirmed_at is None:
            lot.inventory_confirmed_at = timezone.now()
    lot.save()
    return lot


@transaction.atomic
def update_lot_prices(
    *,
    lot: InventoryLot,
    membership: BusinessMembership,
    b2b_amount: Decimal | None,
    b2c_amount: Decimal | None,
    currency: str = "IRR",
) -> None:
    _require(membership, PRICES_EDIT)
    if lot.business_id != membership.business_id:
        raise InventoryError("دسترسی به این محموله وجود ندارد.")
    try:
        set_lot_prices(lot=lot, b2b_amount=b2b_amount, b2c_amount=b2c_amount, currency=currency)
    except ValueError as exc:
        raise InventoryError(str(exc)) from exc


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
    if lot.business_id != membership.business_id:
        raise InventoryError("دسترسی به این محموله وجود ندارد.")

    content_type = getattr(upload, "content_type", "") or ""
    if not content_type.startswith("image/") and not content_type.startswith("video/"):
        # Allow common image uploads without content_type in some browsers
        name = (upload.name or "").lower()
        if not name.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".mov", ".webm")):
            raise InventoryError("فقط تصویر یا ویدیو قابل بارگذاری است.")

    kind = LotMedia.Kind.VIDEO if content_type.startswith("video/") or (upload.name or "").lower().endswith(
        (".mp4", ".mov", ".webm")
    ) else LotMedia.Kind.IMAGE

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
def confirm_lot_inventory(*, lot: InventoryLot, membership: BusinessMembership) -> InventoryLot:
    _require(membership, INVENTORY_CONFIRM)
    if lot.business_id != membership.business_id:
        raise InventoryError("دسترسی به این محموله وجود ندارد.")
    lot.mark_confirmed()
    return lot


@transaction.atomic
def mark_lot_sold(*, lot: InventoryLot, membership: BusinessMembership) -> InventoryLot:
    _require(membership, INVENTORY_EDIT)
    if lot.business_id != membership.business_id:
        raise InventoryError("دسترسی به این محموله وجود ندارد.")
    lot.status = InventoryLot.Status.SOLD
    lot.available_sqm = Decimal("0")
    lot.save(update_fields=["status", "available_sqm", "updated_at"])
    return lot


@transaction.atomic
def hide_lot(*, lot: InventoryLot, membership: BusinessMembership) -> InventoryLot:
    _require(membership, INVENTORY_PUBLISH)
    if lot.business_id != membership.business_id:
        raise InventoryError("دسترسی به این محموله وجود ندارد.")
    lot.status = InventoryLot.Status.HIDDEN
    lot.save(update_fields=["status", "updated_at"])
    return lot


@transaction.atomic
def archive_lot(*, lot: InventoryLot, membership: BusinessMembership) -> InventoryLot:
    _require(membership, INVENTORY_EDIT)
    if lot.business_id != membership.business_id:
        raise InventoryError("دسترسی به این محموله وجود ندارد.")
    lot.archived_at = timezone.now()
    lot.status = InventoryLot.Status.HIDDEN
    lot.save(update_fields=["archived_at", "status", "updated_at"])
    return lot


@transaction.atomic
def duplicate_lot(*, lot: InventoryLot, membership: BusinessMembership) -> InventoryLot:
    _require(membership, INVENTORY_CREATE)
    if lot.business_id != membership.business_id:
        raise InventoryError("دسترسی به این محموله وجود ندارد.")

    clone = InventoryLot.objects.create(
        business=lot.business,
        product=lot.product,
        warehouse=lot.warehouse,
        lot_code=_next_lot_code(lot.business),
        status=InventoryLot.Status.DRAFT,
        visibility=InventoryLot.Visibility.PRIVATE,
        available_sqm=lot.available_sqm,
        original_sqm=lot.original_sqm,
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
    b2b = lot.prices.filter(tier__code="b2b").first()
    b2c = lot.prices.filter(tier__code="b2c").first()
    if b2b or b2c:
        set_lot_prices(
            lot=clone,
            b2b_amount=b2b.amount if b2b else None,
            b2c_amount=b2c.amount if b2c else None,
            currency=(b2b or b2c).currency if (b2b or b2c) else "IRR",
        )
    return clone


def lot_owner_context(lot: InventoryLot, *, can_view_prices: bool = True) -> dict:
    from apps.pricing.services import resolve_visible_prices

    freshness = evaluate_freshness(lot)
    prices = resolve_visible_prices(lot, "owner_staff", can_view_prices=can_view_prices)
    primary = lot.media.filter(is_primary=True).first() or lot.media.first()
    return {
        "lot": lot,
        "freshness": freshness,
        "prices": prices,
        "primary_media": primary,
    }
