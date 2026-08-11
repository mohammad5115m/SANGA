from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

# Entry types that represent a real trade (as opposed to money movement, a manual
# adjustment, or a correction). A trade may be recorded at most once per
# reservation per business — enforced by ``uniq_trade_entry_per_reservation``.
TRADE_ENTRY_TYPES: tuple[str, ...] = ("sale", "purchase")


class LedgerEntry(models.Model):
    """An immutable financial ledger entry for a contact.

    Balance convention (owning business perspective):
      balance > 0  ⇒ the contact owes us   (طلب ما)
      balance < 0  ⇒ we owe the contact     (بدهی ما)

    ``amount`` is always a positive magnitude. ``balance_delta`` is the signed
    effect on the running balance and is the single source of truth for balance
    math. ``balance_after`` is the running balance immediately after this entry,
    computed under a per-contact row lock at posting time. Entries are never
    edited or deleted after posting; corrections are made with reversal entries.
    """

    class Type(models.TextChoices):
        SALE = "sale", "فروش به مخاطب"
        PURCHASE = "purchase", "خرید از مخاطب"
        PAYMENT_RECEIVED = "payment_received", "دریافت وجه"
        PAYMENT_MADE = "payment_made", "پرداخت وجه"
        ADJUST_DEBIT = "adjust_debit", "بدهکار کردن مخاطب"
        ADJUST_CREDIT = "adjust_credit", "بستانکار کردن مخاطب"
        REVERSAL = "reversal", "برگشت سند"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="ledger_entries",
    )
    contact = models.ForeignKey(
        "contacts.Contact",
        on_delete=models.PROTECT,
        related_name="ledger_entries",
    )
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
    related_reservation = models.ForeignKey(
        "reservations.Reservation",
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
    # reversal is posted, so ``uniq_trade_entry_per_reservation`` can free the slot
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
            # Idempotency for trades: a reservation can yield at most one *live*
            # trade entry in a given business's ledger, no matter how many times
            # the seller retries or double-submits. Reversals and money movements
            # are outside the condition, so corrections stay possible. Scoped by
            # business so the (postponed) buyer-side PURCHASE mirror still fits.
            # ``reversed_at__isnull=True`` means a reversed trade releases the
            # slot, so the seller can re-record it with the reservation link.
            models.UniqueConstraint(
                fields=["business", "related_reservation"],
                condition=models.Q(
                    entry_type__in=TRADE_ENTRY_TYPES,
                    related_reservation__isnull=False,
                    reversed_at__isnull=True,
                ),
                name="uniq_trade_entry_per_reservation",
            ),
        ]
        indexes = [
            models.Index(fields=["business", "contact", "created_at"]),
            models.Index(fields=["contact", "created_at"]),
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
    def is_debit(self) -> bool:
        """True when the entry increases what the contact owes us."""
        return self.balance_delta > 0
