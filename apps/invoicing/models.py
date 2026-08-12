"""Sales invoices.

Invoices are historical documents. Everything commercially meaningful on one is
copied onto it at issue time, so yesterday's invoice still reads correctly after
today's price change, rename or deletion.

This is the deliberate opposite of a catalog, which is always live. The two are
the clearest example of the same data serving two purposes that must not share a
representation.

Scope: a simple commercial invoice. No VAT engine, no tax-authority integration,
no fiscal device, no payment gateway. Those are separate products.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class SalesInvoice(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "پیش‌نویس"
        ISSUED = "issued", "صادر شده"
        CANCELLED = "cancelled", "باطل شده"

    class Counterparty(models.TextChoices):
        BUSINESS = "business", "همکار"
        CUSTOMER = "customer", "مشتری"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    seller_business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="sales_invoices",
    )
    # Sequential per seller, allocated under a lock. Stored as text because it is
    # a document identifier that people read aloud, not a number to do arithmetic
    # on, and because the format may gain a prefix later.
    number = models.CharField("شماره فاکتور", max_length=32)

    counterparty_type = models.CharField(
        max_length=20,
        choices=Counterparty.choices,
        default=Counterparty.BUSINESS,
    )
    buyer_business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="received_invoices",
    )
    # A retail buyer with no SANGA account. Never a platform User.
    customer_name = models.CharField("نام مشتری", max_length=150, blank=True)
    customer_phone = models.CharField("موبایل مشتری", max_length=20, blank=True)
    # Snapshot of who was billed, so a later rename does not rewrite the document.
    buyer_name = models.CharField("نام خریدار", max_length=200)

    trade = models.ForeignKey(
        "trading.Trade",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices",
    )

    issue_date = models.DateField("تاریخ صدور")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    total_amount = models.DecimalField(
        "جمع کل",
        max_digits=16,
        decimal_places=2,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    currency = models.CharField(max_length=3, default="IRR")
    notes = models.TextField("توضیحات", blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "فاکتور فروش"
        verbose_name_plural = "فاکتورهای فروش"
        ordering = ["-issue_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["seller_business", "number"], name="uniq_invoice_number_per_seller"),
            models.CheckConstraint(
                condition=(
                    models.Q(counterparty_type="business", buyer_business__isnull=False)
                    | models.Q(counterparty_type="customer", buyer_business__isnull=True)
                ),
                name="invoice_counterparty_matches_type",
            ),
        ]
        indexes = [
            models.Index(fields=["seller_business", "-issue_date"]),
            models.Index(fields=["buyer_business", "-issue_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.number} — {self.buyer_name}"

    @property
    def is_editable(self) -> bool:
        """Only a draft may change. Issuing freezes the document."""
        return self.status == self.Status.DRAFT


class SalesInvoiceItem(models.Model):
    """One line, carrying its own copy of everything it describes.

    ``item`` is a nullable FK kept purely for navigation. Nothing on a rendered
    invoice reads through it: the product may since have been renamed, repriced
    or deleted, and the invoice must not change when it is.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey(SalesInvoice, on_delete=models.CASCADE, related_name="items")
    item = models.ForeignKey(
        "inventory.InventoryLot",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoice_items",
    )

    # --- snapshot ---
    product_name = models.CharField("نام محصول", max_length=200)
    stone_type = models.CharField("نوع سنگ", max_length=100, blank=True)
    grade = models.CharField("سورت", max_length=50, blank=True)
    description = models.CharField("توضیح", max_length=255, blank=True)

    quantity = models.DecimalField(
        "مقدار",
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    unit = models.CharField("واحد", max_length=20, default="متر مربع")
    unit_price = models.DecimalField(
        "قیمت واحد",
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    line_total = models.DecimalField(
        "جمع",
        max_digits=16,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        verbose_name = "ردیف فاکتور"
        verbose_name_plural = "ردیف‌های فاکتور"
        ordering = ["sort_order", "id"]

    def __str__(self) -> str:
        return f"{self.product_name} × {self.quantity}"
