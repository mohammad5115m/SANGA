"""Public customer inquiries.

A customer asks about **several products at once**, because that is how anyone
actually shops for stone: a floor, a facade and a staircase in one conversation.
V1 modelled one inquiry per product, which forced the customer to submit three
times and gave the seller three unrelated leads.

Public customers are never platform Users. :class:`CustomerLead` is a light
identity keyed by phone number, not an account — no login, no password, no
membership. It exists so a seller can see that the same person asked twice.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class CustomerLead(models.Model):
    """A retail customer, identified by phone, scoped to one seller.

    Deliberately thin. This is not a CRM: there are no pipelines, owners, scores
    or activity feeds. It answers one question — «این مشتری قبلاً چه چیزی
    پرسیده؟» — and stops there.

    Scoped per business rather than platform-wide so one seller's customer list
    is not another seller's, which is both a privacy property and the reason two
    sellers can hold different names for the same number without conflict.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="customer_leads",
    )
    name = models.CharField("نام", max_length=150)
    phone = models.CharField("موبایل", max_length=20)
    # Set when the customer completed an OTP challenge at submission. Not a
    # login: it only records that the phone was reachable at that moment.
    phone_verified_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField("یادداشت فروشنده", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "مشتری"
        verbose_name_plural = "مشتریان"
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(fields=["business", "phone"], name="uniq_lead_phone_per_business"),
        ]
        indexes = [
            models.Index(fields=["business", "-updated_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.phone})"

    @property
    def is_verified(self) -> bool:
        return self.phone_verified_at is not None


class Inquiry(models.Model):
    """One request from one customer, covering one or more products."""

    class Status(models.TextChoices):
        # Four states, matching what a seller actually does: see it, call them,
        # finish. V1 had seven, and the middle three were never distinguishable.
        NEW = "new", "جدید"
        CONTACTED = "contacted", "تماس گرفته‌شده"
        CONVERTED = "converted", "تبدیل به فروش"
        CLOSED = "closed", "بسته"

    OPEN_STATUSES = (Status.NEW, Status.CONTACTED)

    class Source(models.TextChoices):
        PUBLIC_SEARCH = "public_search", "جستجوی عمومی"
        STOREFRONT = "storefront", "ویترین"
        ITEM_DETAIL = "item_detail", "صفحه محصول"
        SHARE_LINK = "share_link", "لینک اشتراک"
        CUSTOM_CATALOG = "custom_catalog", "کاتالوگ"
        MARKETPLACE = "marketplace", "بازار همکاران"
        OTHER = "other", "سایر"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="inquiries",
    )
    lead = models.ForeignKey(
        CustomerLead,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="inquiries",
    )
    # Legacy single-product link. New inquiries use InquiryItem; this stays
    # populated for pre-V2 rows and for the one-product shortcut so existing
    # queries and the dashboard keep working.
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

    # Copied from the lead at submission so the inquiry still reads correctly if
    # the customer later gives a different name.
    name = models.CharField("نام", max_length=150)
    phone = models.CharField("موبایل", max_length=20)
    message = models.TextField("پیام", blank=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    source = models.CharField(max_length=32, choices=Source.choices, default=Source.PUBLIC_SEARCH)
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

    @property
    def is_open(self) -> bool:
        return self.status in self.OPEN_STATUSES


class InquiryItem(models.Model):
    """One product the customer asked about, with the quantity they need."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    inquiry = models.ForeignKey(Inquiry, on_delete=models.CASCADE, related_name="items")
    item = models.ForeignKey(
        "inventory.InventoryLot",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inquiry_items",
    )
    # Snapshot so the seller can still read the request after the product is
    # renamed or withdrawn — which is common, since an inquiry often *is* the
    # reason the product changes.
    product_name = models.CharField("نام محصول", max_length=200)
    requested_qty_sqm = models.DecimalField(
        "متراژ درخواستی",
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    note = models.CharField("توضیح", max_length=255, blank=True)

    class Meta:
        verbose_name = "ردیف استعلام"
        verbose_name_plural = "ردیف‌های استعلام"
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["inquiry", "item"], name="uniq_item_per_inquiry"),
        ]

    def __str__(self) -> str:
        return self.product_name
