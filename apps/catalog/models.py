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

    class Mode(models.TextChoices):
        MANUAL = "manual", "انتخاب دستی"
        RULE = "rule", "بر اساس فیلتر"
        HYBRID = "hybrid", "فیلتر + انتخاب دستی"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="custom_catalogs",
    )
    title = models.CharField("عنوان", max_length=200)
    customer_name = models.CharField("نام مشتری", max_length=150, blank=True)
    custom_message = models.TextField("پیام اختصاصی", blank=True)

    mode = models.CharField("نوع کاتالوگ", max_length=20, choices=Mode.choices, default=Mode.MANUAL)
    # A serialized apps.inventory.filters.ItemFilterSpec. Storing the same schema
    # the search bar produces is what makes a rule catalog *literally* a saved
    # search, rather than a second filtering language to keep in step.
    rules = models.JSONField("قوانین", default=dict, blank=True)
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

    @property
    def uses_rules(self) -> bool:
        return self.mode in (self.Mode.RULE, self.Mode.HYBRID)

    @property
    def uses_manual(self) -> bool:
        return self.mode in (self.Mode.MANUAL, self.Mode.HYBRID)


class CustomCatalogItem(models.Model):
    """A manual override on top of whatever the rules select.

    Two kinds, because "add this one extra thing" and "not that one" are both
    normal, and a rule that has to encode an exception stops being readable.
    """

    class Inclusion(models.TextChoices):
        INCLUDE = "include", "افزودن دستی"
        EXCLUDE = "exclude", "حذف دستی"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    catalog = models.ForeignKey(CustomCatalog, on_delete=models.CASCADE, related_name="items")
    lot = models.ForeignKey(
        "inventory.InventoryLot",
        on_delete=models.CASCADE,
        related_name="custom_catalog_items",
    )
    inclusion = models.CharField(max_length=20, choices=Inclusion.choices, default=Inclusion.INCLUDE)
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
