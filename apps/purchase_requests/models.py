from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class PurchaseRequest(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "باز"
        MATCHING = "matching", "در حال تطبیق"
        OFFERED = "offered", "پیشنهاد دریافت‌شده"
        CLOSED = "closed", "بسته"
        CANCELLED = "cancelled", "لغو شده"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="purchase_requests",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_requests_created",
    )
    title = models.CharField("عنوان", max_length=200)
    stone_type = models.CharField("نوع سنگ", max_length=100, blank=True)
    category = models.CharField("دسته‌بندی", max_length=100, blank=True)
    color = models.CharField("رنگ", max_length=100, blank=True)
    application = models.CharField("کاربرد مورد نظر", max_length=150, blank=True)
    required_qty_sqm = models.DecimalField(
        "متراژ مورد نیاز",
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    thickness_mm = models.DecimalField(
        "ضخامت (mm)",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
    )
    length_cm = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    width_cm = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    acceptable_grade = models.CharField("سورت قابل قبول", max_length=50, blank=True)
    budget_amount = models.DecimalField(
        "بودجه تقریبی (به ازای متر)",
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    budget_currency = models.CharField(max_length=3, default="IRR")
    destination_city = models.CharField("شهر مقصد", max_length=100, blank=True)
    required_by = models.DateField("تاریخ نیاز", null=True, blank=True)
    similar_accepted = models.BooleanField("پذیرش مشابه", default=True)
    notes = models.TextField("توضیحات", blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    is_public_to_network = models.BooleanField(
        default=True,
        help_text="قابل مشاهده برای کسب‌وکارهای عضو شبکه (نه حراج عمومی)",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "درخواست خرید"
        verbose_name_plural = "درخواست‌های خرید"
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["business", "status"]),
            models.Index(fields=["stone_type", "color"]),
        ]

    def __str__(self) -> str:
        return self.title


class PurchaseOffer(models.Model):
    """Private seller response — never a public reverse auction."""

    class Status(models.TextChoices):
        SUBMITTED = "submitted", "ارسال‌شده"
        WITHDRAWN = "withdrawn", "پس گرفته‌شده"
        ACCEPTED = "accepted", "پذیرفته‌شده"
        REJECTED = "rejected", "ردشده"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    purchase_request = models.ForeignKey(
        PurchaseRequest,
        on_delete=models.CASCADE,
        related_name="offers",
    )
    seller_business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="purchase_offers",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_offers_created",
    )
    lot = models.ForeignKey(
        "inventory.InventoryLot",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_offers",
    )
    unit_price = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    currency = models.CharField(max_length=3, default="IRR")
    offered_qty_sqm = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    message = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SUBMITTED)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "پیشنهاد فروش"
        verbose_name_plural = "پیشنهادهای فروش"
        constraints = [
            models.UniqueConstraint(
                fields=["purchase_request", "seller_business"],
                condition=models.Q(status="submitted"),
                name="uniq_open_offer_per_seller_pr",
            ),
        ]

    def __str__(self) -> str:
        return f"Offer {self.seller_business} → {self.purchase_request}"
