"""Bilateral agreements, finalized trades, and request-era history.

Current colleague commerce is recorded as a ``TradeProposal`` by either party
and becomes a Trade only after the other party confirms. ``PurchaseRequest`` is
kept so old records remain readable; current UI code does not create new rows.
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

    #: Every status change SANGA is willing to perform, keyed by the status the
    #: request is *actually* in when the lock is taken.
    #:
    #: Written down rather than implied by four separate ``if`` statements
    #: because the dangerous transitions are the ones nobody thought to forbid.
    #: The three terminal states map to nothing at all: a request that has been
    #: completed, rejected or cancelled is history, and a stale browser tab
    #: POSTing against it must be refused rather than allowed to reopen it.
    ALLOWED_TRANSITIONS = {
        Status.SENT: frozenset({Status.ACCEPTED, Status.REJECTED, Status.CANCELLED}),
        # Cancelling after agreement is legitimate — the deal falls through
        # before anything is shipped. Cancelling after *finalization* is not,
        # and is blocked separately because a Trade exists by then.
        Status.ACCEPTED: frozenset({Status.COMPLETED, Status.CANCELLED}),
        Status.COMPLETED: frozenset(),
        Status.REJECTED: frozenset(),
        Status.CANCELLED: frozenset(),
    }

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


class TradeProposal(models.Model):
    """A phone/counter agreement waiting for the other Business to confirm.

    A proposal is deliberately not a Trade, invoice or ledger event. The
    initiator records the facts they already agreed outside SANGA; only the
    counterparty's explicit confirmation turns those snapshots into immutable
    commercial history.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "پیش‌نویس"
        PENDING = "pending", "در انتظار تأیید"
        CONFIRMED = "confirmed", "تأیید و نهایی شد"
        REJECTED = "rejected", "رد شد"
        CANCELLED = "cancelled", "لغو شد"

    OPEN_STATUSES = (Status.DRAFT, Status.PENDING)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    seller_business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="sale_proposals",
    )
    buyer_business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.PROTECT,
        related_name="purchase_proposals",
    )
    initiated_by_business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.PROTECT,
        related_name="initiated_trade_proposals",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="trade_proposals_created",
    )
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="trade_proposals_confirmed",
    )
    submission_id = models.UUIDField(null=True, blank=True, editable=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    total_amount = models.DecimalField(
        "مبلغ کل",
        max_digits=16,
        decimal_places=2,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    currency = models.CharField(max_length=3, default="IRR")
    note = models.TextField("توضیح", blank=True)
    trade = models.OneToOneField(
        "Trade",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="source_proposal",
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "توافق معامله"
        verbose_name_plural = "توافق‌های معامله"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(seller_business=models.F("buyer_business")),
                name="trade_proposal_not_self",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(initiated_by_business=models.F("seller_business"))
                    | models.Q(initiated_by_business=models.F("buyer_business"))
                ),
                name="trade_proposal_initiator_is_party",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="confirmed", trade__isnull=False)
                    | (~models.Q(status="confirmed") & models.Q(trade__isnull=True))
                ),
                name="trade_proposal_trade_when_confirmed",
            ),
            models.UniqueConstraint(
                fields=["initiated_by_business", "submission_id"],
                condition=models.Q(submission_id__isnull=False),
                name="uniq_trade_proposal_submission",
            ),
        ]
        indexes = [
            models.Index(fields=["seller_business", "status", "-created_at"]),
            models.Index(fields=["buyer_business", "status", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.seller_business_id} → {self.buyer_business_id} ({self.status})"

    def other_business(self, business):
        if business.pk == self.seller_business_id:
            return self.buyer_business
        if business.pk == self.buyer_business_id:
            return self.seller_business
        return None


class TradeProposalItem(models.Model):
    """One frozen line the counterparty is being asked to confirm."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    proposal = models.ForeignKey(TradeProposal, on_delete=models.CASCADE, related_name="items")
    item = models.ForeignKey(
        "inventory.InventoryLot",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="trade_proposal_items",
    )
    product_name = models.CharField("نام محصول", max_length=200)
    stone_type = models.CharField("نوع سنگ", max_length=100, blank=True)
    grade = models.CharField("سورت", max_length=50, blank=True)
    quantity = models.DecimalField(
        "متراژ",
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
        verbose_name = "ردیف توافق معامله"
        verbose_name_plural = "ردیف‌های توافق معامله"
        ordering = ["sort_order", "id"]

    def __str__(self) -> str:
        return f"{self.product_name} × {self.quantity}"


class Trade(models.Model):
    """A finalized commercial transaction: the header of one commercial event.

    The lines live on :class:`TradeItem`. A stone seller sells a colleague
    travertine, marble and crystal in one conversation, and modelling that as
    three trades meant three invoices, three ledger entries and three balances to
    reconcile — so the workaround for a modelling gap produced worse bookkeeping
    than the thing it worked around. One sale is one Trade, one total, one entry
    per party and one invoice, however many stones it covers.

    Carries its own copy of the commercial facts, as do its lines. A trade
    recorded today must still read correctly after the product is renamed,
    repriced, marked unavailable or deleted, so nothing here is looked up through
    ``item`` at display time — the FK exists for navigation, not for rendering
    history.

    **The single-line columns below are legacy.** They predate ``TradeItem`` and
    stay populated for one-line sales, which is every historical row and every
    request-driven sale, so nothing that already reads them breaks. They are
    blank on a multi-line trade, where ``items`` is the only truth. New readers
    must go through ``items``.
    """

    class Counterparty(models.TextChoices):
        BUSINESS = "business", "همکار"
        CUSTOMER = "customer", "مشتری"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    #: Identifies the *submission* that asked for this sale, as opposed to the
    #: sale itself.
    #:
    #: A request-driven sale is idempotent for free, because ``purchase_request``
    #: is a OneToOneField and finalization holds the request's row lock. A direct
    #: sale has no request, so it had neither protection: a double-click, a proxy
    #: retry or two tabs produced two Trades describing one real-world sale, and
    #: because they were genuinely different rows every per-trade constraint was
    #: satisfied while the colleague's balance moved twice.
    #:
    #: Minted by the form before the user submits, so every retry of one attempt
    #: carries the same value. Null for sales not recorded through that form.
    submission_id = models.UUIDField(null=True, blank=True, editable=False)

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

    # --- legacy single-line snapshot; see the class docstring ---
    product_name = models.CharField("نام محصول", max_length=200, blank=True)
    stone_type = models.CharField("نوع سنگ", max_length=100, blank=True)
    grade = models.CharField("سورت", max_length=50, blank=True)

    quantity_sqm = models.DecimalField(
        "متراژ",
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    unit_price = models.DecimalField(
        "قیمت واحد",
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
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
            # One submission, one sale. The service looks the trade up under the
            # seller's row lock before creating one, but a lookup is not an
            # idempotency mechanism on its own — two connections can both find
            # nothing. This is the invariant that holds when they do, and the
            # service turns the violation back into the winning Trade.
            #
            # Scoped by seller so two businesses can never collide, and partial
            # so historical rows and non-form callers stay legal.
            models.UniqueConstraint(
                fields=["seller_business", "submission_id"],
                condition=models.Q(submission_id__isnull=False),
                name="uniq_trade_per_submission",
            ),
        ]
        indexes = [
            models.Index(fields=["seller_business", "-finalized_at"]),
            models.Index(fields=["buyer_business", "-finalized_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.summary_label} = {self.total_amount}"

    @property
    def counterparty_label(self) -> str:
        if self.counterparty_type == self.Counterparty.BUSINESS and self.buyer_business_id:
            return self.buyer_business.name
        return self.customer_name or "مشتری"

    @property
    def summary_label(self) -> str:
        """What to call this sale in a list, a heading or a notification.

        A one-line sale is named after what was sold, because that is how the
        seller remembers it. A multi-line sale is named by its size: inventing a
        headline product for a basket of three stones would be a snapshot of
        something that never happened.
        """
        lines = list(self.items.all())
        if len(lines) == 1:
            return lines[0].product_name
        if lines:
            return f"{len(lines)} قلم کالا"
        return self.product_name or "معامله"


class TradeItem(models.Model):
    """One line of a finalized sale, carrying its own copy of what it describes.

    Immutable history, exactly like a ``SalesInvoiceItem``: ``item`` is a
    nullable FK kept purely for navigation, and nothing displayed reads through
    it. Renaming, repricing or deleting the product must not rewrite a sale that
    already happened.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    trade = models.ForeignKey(Trade, on_delete=models.CASCADE, related_name="items")
    item = models.ForeignKey(
        "inventory.InventoryLot",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="trade_items",
    )

    # --- snapshot ---
    product_name = models.CharField("نام محصول", max_length=200)
    stone_type = models.CharField("نوع سنگ", max_length=100, blank=True)
    grade = models.CharField("سورت", max_length=50, blank=True)

    quantity = models.DecimalField(
        "متراژ",
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
        verbose_name = "ردیف معامله"
        verbose_name_plural = "ردیف‌های معامله"
        ordering = ["sort_order", "id"]

    def __str__(self) -> str:
        return f"{self.product_name} × {self.quantity}"
