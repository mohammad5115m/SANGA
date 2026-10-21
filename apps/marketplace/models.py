"""Authenticated, non-financial partner inquiries from the B2B marketplace."""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class PartnerInquiryBatch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    buyer_business = models.ForeignKey(
        "businesses.Business", on_delete=models.PROTECT, related_name="partner_inquiry_batches"
    )
    submission_id = models.UUIDField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["buyer_business", "submission_id"],
                name="uniq_partner_inquiry_batch_submission",
            )
        ]

    def __str__(self) -> str:
        return f"استعلام گروهی {self.id}"


class PartnerInquiry(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "پیش‌نویس"
        SENT = "sent", "ارسال‌شده"
        RESPONDED = "responded", "پاسخ‌داده‌شده"
        CONVERTED = "converted_to_invoice", "تبدیل‌شده به فاکتور"
        CLOSED = "closed", "بسته‌شده"
        CANCELLED = "cancelled", "لغوشده"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(PartnerInquiryBatch, on_delete=models.PROTECT, related_name="inquiries")
    buyer_business = models.ForeignKey(
        "businesses.Business", on_delete=models.PROTECT, related_name="sent_partner_inquiries"
    )
    seller_business = models.ForeignKey(
        "businesses.Business", on_delete=models.PROTECT, related_name="received_partner_inquiries"
    )
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.SENT)
    buyer_note = models.CharField(max_length=500, blank=True)
    seller_note = models.CharField(max_length=500, blank=True)
    converted_invoice = models.OneToOneField(
        "invoicing.SalesInvoice",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="source_partner_inquiry",
    )
    sent_at = models.DateTimeField()
    responded_at = models.DateTimeField(null=True, blank=True)
    converted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-sent_at"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(buyer_business=models.F("seller_business")),
                name="partner_inquiry_not_self",
            ),
            models.UniqueConstraint(fields=["batch", "seller_business"], name="uniq_inquiry_seller_per_batch"),
        ]

    def __str__(self) -> str:
        return f"{self.buyer_business} → {self.seller_business}"


class PartnerInquiryItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    inquiry = models.ForeignKey(PartnerInquiry, on_delete=models.PROTECT, related_name="items")
    item = models.ForeignKey("inventory.InventoryLot", on_delete=models.SET_NULL, null=True, blank=True)
    product_name = models.CharField(max_length=200)
    stone_type = models.CharField(max_length=100, blank=True)
    quantity_requested = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    unit = models.CharField(max_length=20, default="متر مربع")
    availability_snapshot = models.CharField(max_length=40, blank=True)
    availability_checked_at = models.DateTimeField(null=True, blank=True)
    price_snapshot = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True)
    price_requires_confirmation = models.BooleanField(default=True)
    offered_quantity = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    offered_unit_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    seller_note = models.CharField(max_length=250, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity_requested__gt=0),
                name="partner_inquiry_quantity_positive",
            )
        ]

    def __str__(self) -> str:
        return f"{self.product_name} · {self.quantity_requested} {self.unit}"
