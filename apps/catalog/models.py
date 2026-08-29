from __future__ import annotations

import secrets
import uuid

from django.db import models
from django.utils import timezone


def generate_share_token() -> str:
    return secrets.token_urlsafe(16)


class CustomCatalog(models.Model):
    """A shareable, **live** selection of products.

    A catalog always renders current data: today's price, today's stock, today's
    photos. That is the deliberate opposite of an invoice, which is frozen at
    issue time. The two are the clearest example of the same products serving two
    purposes that must not share a representation.

    > **Catalog = current. Invoice = historical.**
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="custom_catalogs",
    )
    title = models.CharField("عنوان", max_length=200)
    customer_name = models.CharField("نام مشتری", max_length=150, blank=True)
    custom_message = models.TextField("پیام اختصاصی", blank=True)

    share_token = models.CharField(max_length=64, unique=True, default=generate_share_token, editable=False)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    view_count = models.PositiveIntegerField(default=0)
    first_viewed_at = models.DateTimeField(null=True, blank=True)
    last_viewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "کاتالوگ اختصاصی"
        verbose_name_plural = "کاتالوگ‌های اختصاصی"

    def __str__(self) -> str:
        return self.title

    @property
    def is_expired(self) -> bool:
        return bool(self.expires_at and timezone.now() >= self.expires_at)

    @property
    def is_publicly_accessible(self) -> bool:
        return self.is_active and not self.is_expired


class CustomCatalogItem(models.Model):
    """One inventory item intentionally selected for a live catalog."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    catalog = models.ForeignKey(CustomCatalog, on_delete=models.CASCADE, related_name="items")
    lot = models.ForeignKey(
        "inventory.InventoryLot",
        on_delete=models.CASCADE,
        related_name="custom_catalog_items",
    )
    sort_order = models.PositiveIntegerField(default=0)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "آیتم کاتالوگ"
        verbose_name_plural = "آیتم‌های کاتالوگ"
        constraints = [
            models.UniqueConstraint(fields=["catalog", "lot"], name="uniq_lot_per_custom_catalog"),
        ]

    def __str__(self) -> str:
        return f"{self.catalog.title} :: {self.lot.lot_code}"


class StorefrontCollection(models.Model):
    """A reusable seller-controlled merchandising section on the storefront."""

    class SuggestionKind(models.TextChoices):
        NONE = "", "بدون پیشنهاد خودکار"
        ECONOMIC = "economic", "قیمت‌های اقتصادی"
        FRESH = "fresh", "تازه‌های ویترین"
        EXTERIOR = "exterior", "مناسب نمای بیرونی"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="storefront_collections",
    )
    title = models.CharField("عنوان", max_length=120)
    description = models.CharField("توضیح کوتاه", max_length=240, blank=True)
    is_active = models.BooleanField("نمایش در ویترین", default=False)
    sort_order = models.PositiveIntegerField(default=0)
    suggestion_kind = models.CharField(
        "پیشنهاد سیستمی",
        max_length=20,
        choices=SuggestionKind.choices,
        blank=True,
        default=SuggestionKind.NONE,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "created_at"]
        verbose_name = "مجموعه ویترین"
        verbose_name_plural = "مجموعه‌های ویترین"
        constraints = [
            models.UniqueConstraint(
                fields=["business", "title"],
                name="uniq_storefront_collection_title_per_business",
            ),
        ]
        indexes = [models.Index(fields=["business", "is_active", "sort_order"])]

    def __str__(self) -> str:
        return self.title


class StorefrontCollectionItem(models.Model):
    """An explicitly ordered product selected for a storefront collection."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    collection = models.ForeignKey(
        StorefrontCollection,
        on_delete=models.CASCADE,
        related_name="items",
    )
    lot = models.ForeignKey(
        "inventory.InventoryLot",
        on_delete=models.CASCADE,
        related_name="storefront_collection_items",
    )
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "محصول مجموعه ویترین"
        verbose_name_plural = "محصولات مجموعه ویترین"
        constraints = [
            models.UniqueConstraint(
                fields=["collection", "lot"],
                name="uniq_lot_per_storefront_collection",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.collection.title} :: {self.lot.lot_code}"
