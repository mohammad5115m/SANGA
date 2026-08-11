from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class Inquiry(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "جدید"
        VIEWED = "viewed", "دیده‌شده"
        CONTACTED = "contacted", "تماس گرفته‌شده"
        NEGOTIATING = "negotiating", "در حال مذاکره"
        CONVERTED = "converted", "تبدیل‌شده"
        CLOSED = "closed", "بسته"
        LOST = "lost", "از دست‌رفته"

    class Source(models.TextChoices):
        STOREFRONT = "storefront", "ویترین"
        LOT_DETAIL = "lot_detail", "صفحه محموله"
        CUSTOM_CATALOG = "custom_catalog", "کاتالوگ اختصاصی"
        SHARE = "share", "اشتراک‌گذاری"
        OTHER = "other", "سایر"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="inquiries",
    )
    lot = models.ForeignKey(
        "inventory.InventoryLot",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inquiries",
    )
    custom_catalog = models.ForeignKey(
        "catalog.CustomCatalog",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inquiries",
    )
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inquiries",
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_inquiries",
    )
    name = models.CharField("نام", max_length=150)
    phone = models.CharField("موبایل", max_length=20)
    message = models.TextField("پیام", blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    source = models.CharField(max_length=32, choices=Source.choices, default=Source.STOREFRONT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    viewed_at = models.DateTimeField(null=True, blank=True)
    contacted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "استعلام"
        verbose_name_plural = "استعلام‌ها"
        indexes = [
            models.Index(fields=["business", "status", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} / {self.business}"
