from __future__ import annotations

import uuid
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.text import slugify


class Product(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="products",
    )
    commercial_name = models.CharField("نام تجاری", max_length=200)
    slug = models.SlugField(max_length=220, allow_unicode=True)
    stone_type = models.CharField("نوع سنگ", max_length=100, blank=True)
    quarry_region = models.CharField("معدن/منطقه", max_length=150, blank=True)
    primary_color = models.CharField("رنگ غالب", max_length=100, blank=True)
    pattern = models.CharField("طرح/بافت", max_length=150, blank=True)
    vein_notes = models.CharField(max_length=255, blank=True)
    applications = models.JSONField(default=list, blank=True)
    interior_suitable = models.BooleanField(default=True)
    exterior_suitable = models.BooleanField(default=False)
    technical_notes = models.TextField(blank=True)
    description_public = models.TextField("توضیح مشتری", blank=True)
    description_professional = models.TextField("توضیح حرفه‌ای", blank=True)
    alt_names = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "محصول"
        verbose_name_plural = "محصولات"
        ordering = ["commercial_name"]
        constraints = [
            models.UniqueConstraint(fields=["business", "slug"], name="uniq_product_slug_per_business"),
        ]

    def __str__(self) -> str:
        return self.commercial_name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.commercial_name, allow_unicode=True) or "product"
            candidate = base
            idx = 1
            while Product.objects.filter(business=self.business, slug=candidate).exclude(pk=self.pk).exists():
                idx += 1
                candidate = f"{base}-{idx}"
            self.slug = candidate
        super().save(*args, **kwargs)


class InventoryLot(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "پیش‌نویس"
        AVAILABLE = "available", "موجود"
        RESERVATION_PENDING = "reservation_pending", "در انتظار رزرو"
        RESERVED = "reserved", "رزرو شده"
        PARTIALLY_SOLD = "partially_sold", "فروش جزئی"
        SOLD = "sold", "فروخته‌شده"
        EXPIRED = "expired", "منقضی"
        HIDDEN = "hidden", "مخفی"
        NEEDS_CONFIRMATION = "needs_confirmation", "نیاز به تأیید"

    class Visibility(models.TextChoices):
        PRIVATE = "private", "داخلی"
        # There is no per-lot allowlist: SELECTED_PARTNERS is a legacy alias of
        # ALL_PARTNERS and both mean "approved partners only". The value is kept
        # so existing rows stay readable; new lots should use ALL_PARTNERS.
        SELECTED_PARTNERS = "selected_partners", "شرکای تأییدشده (قدیمی)"
        ALL_PARTNERS = "all_partners", "شرکای تأییدشده"
        CUSTOMER_CATALOG = "customer_catalog", "کاتالوگ مشتری"
        PUBLIC = "public", "عمومی"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="lots",
    )
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="lots")
    warehouse = models.ForeignKey(
        "businesses.Warehouse",
        on_delete=models.PROTECT,
        related_name="lots",
    )
    lot_code = models.CharField("کد محموله", max_length=64)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.DRAFT)
    visibility = models.CharField(
        max_length=32,
        choices=Visibility.choices,
        default=Visibility.PRIVATE,
    )
    available_sqm = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    original_sqm = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    slab_count = models.PositiveIntegerField(null=True, blank=True)
    bundle_count = models.PositiveIntegerField(null=True, blank=True)
    length_cm = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    width_cm = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    thickness_mm = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    grade = models.CharField(max_length=50, blank=True)
    processing_type = models.CharField(max_length=100, blank=True)
    min_sale_qty = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    ready_for_loading_at = models.DateField(null=True, blank=True)
    photographed_at = models.DateField(null=True, blank=True)
    inventory_confirmed_at = models.DateTimeField(null=True, blank=True)
    offer_expires_at = models.DateTimeField(null=True, blank=True)
    description = models.TextField(blank=True)
    defect_notes = models.TextField(blank=True)
    is_featured = models.BooleanField(default=False)
    is_urgent_sale = models.BooleanField(default=False)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "محموله موجودی"
        verbose_name_plural = "محموله‌های موجودی"
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(fields=["business", "lot_code"], name="uniq_lot_code_per_business"),
            models.CheckConstraint(
                condition=models.Q(available_sqm__gte=0),
                name="lot_available_sqm_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(original_sqm__gte=0),
                name="lot_original_sqm_nonnegative",
            ),
        ]
        indexes = [
            models.Index(fields=["business", "status"]),
            models.Index(fields=["business", "inventory_confirmed_at"]),
            models.Index(fields=["visibility", "status", "updated_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.lot_code} — {self.product.commercial_name}"

    def mark_confirmed(self) -> None:
        self.inventory_confirmed_at = timezone.now()
        if self.status == self.Status.NEEDS_CONFIRMATION:
            self.status = self.Status.AVAILABLE
        self.save(update_fields=["inventory_confirmed_at", "status", "updated_at"])


class LotMedia(models.Model):
    class Kind(models.TextChoices):
        IMAGE = "image", "تصویر"
        VIDEO = "video", "ویدیو"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lot = models.ForeignKey(InventoryLot, on_delete=models.CASCADE, related_name="media")
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.IMAGE)
    file = models.FileField(upload_to="lot_media/")
    thumbnail = models.ImageField(upload_to="lot_media/thumbs/", blank=True, null=True)
    caption = models.CharField(max_length=255, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "created_at"]
        verbose_name = "رسانه محموله"
        verbose_name_plural = "رسانه‌های محموله"

    def __str__(self) -> str:
        return f"{self.lot.lot_code} media"
