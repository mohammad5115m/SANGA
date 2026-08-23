from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

# Entry types that represent a real trade (as opposed to money movement, a manual
# adjustment, or a correction). A finalized Trade may be recorded at most once per
# business — enforced by ``uniq_trade_entry_per_trade``.
TRADE_ENTRY_TYPES: tuple[str, ...] = ("sale", "purchase")


class LedgerEntry(models.Model):
    """An immutable financial ledger entry against one counterparty.

    Balance convention (owning business's books, standard bookkeeping terms):
      balance > 0  ⇒ the counterparty is «بدهکار» — a receivable of the business
      balance < 0  ⇒ the counterparty is «بستانکار» — a payable of the business
      balance == 0 ⇒ «تسویه»

    ``amount`` is always a positive magnitude. ``balance_delta`` is the signed
    effect on the running balance and is the single source of truth for balance
    math. ``balance_after`` is the running balance immediately after this entry,
    computed under a row lock at posting time. Entries are never edited or
    deleted after posting; corrections are made with reversal entries.

    **Counterparty identity.** V2 keys the ledger on the colleague's *Business*
    rather than on a hand-typed Contact, so two people at the same company can no
    longer become two different debtors. Rows that predate V2 and could not be
    mapped to a Business keep ``contact`` and carry the contact's name in
    ``legacy_counterparty_name``; they stay readable and are never posted to
    again. See docs/accounting.md.
    """

    class Type(models.TextChoices):
        SALE = "sale", "فروش"
        PURCHASE = "purchase", "خرید"
        PAYMENT_RECEIVED = "payment_received", "دریافت"
        PAYMENT_MADE = "payment_made", "پرداخت"
        ADJUST_DEBIT = "adjust_debit", "اصلاح بدهکار"
        ADJUST_CREDIT = "adjust_credit", "اصلاح بستانکار"
        REVERSAL = "reversal", "برگشت سند"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="ledger_entries",
    )
    counterparty_business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="counterparty_ledger_entries",
        verbose_name="همکار",
    )
    local_counterparty = models.ForeignKey(
        "invoicing.LocalCounterparty",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ledger_entries",
        verbose_name="همکار محلی",
    )
    # Legacy only. Kept so pre-V2 rows whose Contact had no linked Business stay
    # queryable under the name they were filed under; guessing a Business for
    # them would corrupt a real balance.
    contact = models.ForeignKey(
        "contacts.Contact",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ledger_entries",
    )
    legacy_counterparty_name = models.CharField(max_length=200, blank=True)

    entry_type = models.CharField(max_length=20, choices=Type.choices)
    amount = models.DecimalField(
        "مبلغ",
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    balance_delta = models.DecimalField(max_digits=14, decimal_places=2)
    balance_after = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3, default="IRR")
    description = models.CharField("شرح", max_length=255, blank=True)
    reference = models.CharField("مرجع/شماره سند", max_length=100, blank=True)
    occurred_on = models.DateField("تاریخ")

    related_lot = models.ForeignKey(
        "inventory.InventoryLot",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ledger_entries",
    )
    # The authoritative link for a V2 sale. Set when the entry was posted by
    # finalizing a Trade, which is the one event that may move the books; the
    # unique constraint below hangs off it.
    related_trade = models.ForeignKey(
        "trading.Trade",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ledger_entries",
    )
    related_invoice = models.ForeignKey(
        "invoicing.SalesInvoice",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ledger_entries",
    )
    idempotency_key = models.CharField(max_length=140, blank=True, default="", editable=False)
    # Legacy: set when a trade was recorded from an accepted demand-board offer.
    # The workflow is gone but the rows and their idempotency slot remain.
    related_offer = models.ForeignKey(
        "purchase_requests.PurchaseOffer",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ledger_entries",
    )
    reverses = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reversal_entries",
    )

    # Bookkeeping flag, not financial data: stamped on the *original* entry when a
    # reversal is posted, so ``uniq_trade_entry_per_offer`` can free the slot
    # and the trade can be re-recorded correctly. Because ``save()`` blocks updates
    # by design, this is only ever written through a queryset ``.update()`` inside
    # ``services.reverse_entry`` — see docs/accounting.md. No amount, delta, or
    # balance is ever touched this way.
    reversed_at = models.DateTimeField(null=True, blank=True, db_index=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ledger_entries_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "سند حسابداری"
        verbose_name_plural = "اسناد حسابداری"
        ordering = ["created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="ledger_amount_positive",
            ),
            # Idempotency for trades started from an accepted offer: one offer can
            # yield at most one *live* trade entry in a given business's ledger, no
            # matter how many times the form is retried or double-submitted.
            # Reversals and money movements are outside the condition, so
            # corrections stay possible. Scoped by business so both sides of the
            # same offer can record their own entry (seller SALE, buyer PURCHASE).
            # ``reversed_at__isnull=True`` means a reversed trade releases the
            # slot, so it can be re-recorded with the offer link intact.
            models.UniqueConstraint(
                fields=["business", "related_offer"],
                condition=models.Q(
                    entry_type__in=TRADE_ENTRY_TYPES,
                    related_offer__isnull=False,
                    reversed_at__isnull=True,
                ),
                name="uniq_trade_entry_per_offer",
            ),
            # The V2 equivalent, and the reason finalizing a sale twice cannot
            # double-post: one Trade yields at most one *live* trade entry in a
            # given business's ledger, however many times the request is
            # retried. Scoped by business so both sides could record their own.
            # ``reversed_at__isnull=True`` frees the slot after a reversal, so a
            # corrected trade can be re-recorded with its link intact.
            models.UniqueConstraint(
                fields=["business", "related_trade"],
                condition=models.Q(
                    entry_type__in=TRADE_ENTRY_TYPES,
                    related_trade__isnull=False,
                    reversed_at__isnull=True,
                ),
                name="uniq_trade_entry_per_trade",
            ),
            models.UniqueConstraint(
                fields=["idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="uniq_ledger_idempotency_key",
            ),
        ]
        indexes = [
            models.Index(
                fields=["business", "counterparty_business", "created_at"],
                name="accounting__biz_cpty_idx",
            ),
            models.Index(fields=["business", "contact", "created_at"]),
            models.Index(fields=["business", "local_counterparty", "created_at"]),
            models.Index(fields=["business", "occurred_on"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_entry_type_display()} · {self.amount} {self.currency}"

    def save(self, *args, **kwargs):
        # Immutable after posting: block any update to an existing row.
        if self.pk and not self._state.adding:
            raise ValidationError("سند حسابداری پس از ثبت قابل تغییر نیست.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("سند حسابداری قابل حذف نیست؛ برای اصلاح از «برگشت سند» استفاده کنید.")

    @property
    def counterparty_label(self) -> str:
        """Who this entry is against, however the row was filed."""
        if self.counterparty_business_id:
            return self.counterparty_business.name
        if self.local_counterparty_id:
            return self.local_counterparty.name
        return self.legacy_counterparty_name or "—"

    @property
    def is_debit(self) -> bool:
        """True when the entry belongs in the «بدهکار» column of the statement."""
        return self.balance_delta > 0

    @property
    def is_credit(self) -> bool:
        """True when the entry belongs in the «بستانکار» column of the statement.

        Mutually exclusive with ``is_debit``: ``amount`` is always > 0 and every
        entry type moves the balance in exactly one direction, so ``balance_delta``
        is never zero and an amount is never shown in both columns.
        """
        return self.balance_delta < 0
