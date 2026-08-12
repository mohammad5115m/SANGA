"""Product-bound purchase requests and finalized trades.

Replaces the v1 demand board, where a buyer described what they wanted in free
text, sellers guessed at it with private offers, and a matcher tried to pair
them. Nothing about that produced a sale that either side could point at.

In V2 a purchase request always references **one existing product**. There is
nothing to match, because the buyer already found the thing they want.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class PurchaseRequest(models.Model):
    """A buyer asking to buy a specific product from a specific seller.

    Accepting one is **not** a sale. The seller agrees on quantity and price
    here, then performs a separate, deliberate «نهایی کردن فروش» that creates a
    :class:`Trade` and posts to the ledger. Preliminary agreements that never
    become sales must not reach the books.
    """

    class Status(models.TextChoices):
        SENT = "sent", "ارسال شده"
        ACCEPTED = "accepted", "توافق شده"
        REJECTED = "rejected", "رد شده"
        CANCELLED = "cancelled", "لغو شده"
        COMPLETED = "completed", "فروش نهایی شد"

    #: Statuses in which the request is still waiting on somebody.
    OPEN_STATUSES = (Status.SENT, Status.ACCEPTED)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    item = models.ForeignKey(
        "inventory.InventoryLot",
        on_delete=models.PROTECT,
        related_name="purchase_requests",
        verbose_name="محصول",
    )
    # Denormalized from item.business so a seller's inbox is one indexed query
    # and does not depend on the product still existing in its original shape.
    seller_business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="received_purchase_requests",
    )
    buyer_business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="sent_purchase_requests",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        # Not "purchase_requests_created": the retired demand board model of the
        # same name still owns that accessor, and it keeps it so its historical
        # rows stay reachable.
        related_name="product_purchase_requests_created",
    )

    # What the buyer asked for.
    requested_qty_sqm = models.DecimalField(
        "متراژ درخواستی",
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    proposed_unit_price = models.DecimalField(
        "قیمت پیشنهادی",
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    buyer_note = models.TextField("توضیح خریدار", blank=True)

    # What the seller agreed to. Separate columns rather than overwriting the
    # buyer's numbers: "you asked for 200 at 1.5m, I can do 180 at 1.6m" is the
    # normal conversation, and both halves of it matter afterwards.
    final_qty_sqm = models.DecimalField(
        "متراژ نهایی",
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    final_unit_price = models.DecimalField(
        "قیمت نهایی",
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    seller_note = models.TextField("توضیح فروشنده", blank=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SENT)
    currency = models.CharField(max_length=3, default="IRR")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "درخواست خرید"
        verbose_name_plural = "درخواست‌های خرید"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(seller_business=models.F("buyer_business")),
                name="purchase_request_not_self",
            ),
        ]
        indexes = [
            models.Index(fields=["seller_business", "status", "-created_at"]),
            models.Index(fields=["buyer_business", "status", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.buyer_business_id} → {self.seller_business_id} ({self.status})"

    @property
    def agreed_qty_sqm(self) -> Decimal:
        return self.final_qty_sqm if self.final_qty_sqm is not None else self.requested_qty_sqm

    @property
    def agreed_unit_price(self) -> Decimal | None:
        if self.final_unit_price is not None:
            return self.final_unit_price
        return self.proposed_unit_price

    @property
    def agreed_total(self) -> Decimal | None:
        price = self.agreed_unit_price
        if price is None:
            return None
        return (price * self.agreed_qty_sqm).quantize(Decimal("0.01"))

    @property
    def is_open(self) -> bool:
        return self.status in self.OPEN_STATUSES


class Trade(models.Model):
    """A finalized commercial transaction.

    Carries its own copy of the commercial facts. A trade recorded today must
    still read correctly after the product is renamed, repriced, marked
    unavailable or deleted, so nothing here is looked up through ``item`` at
    display time — the FK exists for navigation, not for rendering history.
    """

    class Counterparty(models.TextChoices):
        BUSINESS = "business", "همکار"
        CUSTOMER = "customer", "مشتری"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    seller_business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="trades",
    )
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
        related_name="purchases",
    )
    # For a walk-in customer who has no SANGA account. Never a platform User.
    customer_name = models.CharField("نام مشتری", max_length=150, blank=True)
    customer_phone = models.CharField("موبایل مشتری", max_length=20, blank=True)

    item = models.ForeignKey(
        "inventory.InventoryLot",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="trades",
    )
    purchase_request = models.OneToOneField(
        PurchaseRequest,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="trade",
    )

    # --- historical snapshot ---
    product_name = models.CharField("نام محصول", max_length=200)
    stone_type = models.CharField("نوع سنگ", max_length=100, blank=True)
    grade = models.CharField("سورت", max_length=50, blank=True)

    quantity_sqm = models.DecimalField(
        "متراژ",
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    unit_price = models.DecimalField(
        "قیمت واحد",
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    total_amount = models.DecimalField(
        "مبلغ کل",
        max_digits=16,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    currency = models.CharField(max_length=3, default="IRR")
    note = models.TextField("توضیح", blank=True)

    finalized_at = models.DateTimeField("تاریخ نهایی شدن")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="trades_finalized",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "معامله"
        verbose_name_plural = "معاملات"
        ordering = ["-finalized_at"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(counterparty_type="business", buyer_business__isnull=False)
                    | models.Q(counterparty_type="customer", buyer_business__isnull=True)
                ),
                name="trade_counterparty_matches_type",
            ),
        ]
        indexes = [
            models.Index(fields=["seller_business", "-finalized_at"]),
            models.Index(fields=["buyer_business", "-finalized_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.product_name} × {self.quantity_sqm} = {self.total_amount}"

    @property
    def counterparty_label(self) -> str:
        if self.counterparty_type == self.Counterparty.BUSINESS and self.buyer_business_id:
            return self.buyer_business.name
        return self.customer_name or "مشتری"
