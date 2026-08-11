from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.businesses.models import Business, BusinessMembership
from apps.businesses.permissions import LEDGER_MANAGE
from apps.contacts.models import Contact
from apps.reservations.models import Reservation

from .models import LedgerEntry
from .selectors import trade_entry_for_reservation

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")

TRADE_ALREADY_RECORDED = "سند مالی این معامله قبلاً ثبت شده است."

# Effect of each entry type on the balance (owning-business perspective):
# +1 increases what the contact owes us; -1 decreases it.
DIRECTION: dict[str, int] = {
    LedgerEntry.Type.SALE: +1,
    LedgerEntry.Type.PAYMENT_MADE: +1,
    LedgerEntry.Type.ADJUST_DEBIT: +1,
    LedgerEntry.Type.PURCHASE: -1,
    LedgerEntry.Type.PAYMENT_RECEIVED: -1,
    LedgerEntry.Type.ADJUST_CREDIT: -1,
}

ADJUSTMENT_TYPES = {LedgerEntry.Type.ADJUST_DEBIT, LedgerEntry.Type.ADJUST_CREDIT}


class LedgerError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class LedgerDuplicateError(LedgerError):
    """The trade was already recorded; the caller should treat this as a no-op.

    A subclass of ``LedgerError`` so existing handlers keep working, but distinct
    so the UI can say «قبلاً ثبت شده» instead of showing a failure.
    """

    def __init__(
        self, message: str = TRADE_ALREADY_RECORDED, *, existing: LedgerEntry | None = None
    ) -> None:
        self.existing = existing
        super().__init__(message)


@dataclass(frozen=True)
class TradeEntryRequest:
    """Opt-in payload for recording the financial result of a trade.

    Passed to ``reservations.services.convert_reservation`` when the caller wants
    the conversion and its ledger entry in one transaction. Conversion stays
    non-financial whenever this is omitted.
    """

    contact: Contact
    amount: Decimal
    description: str = ""
    reference: str = ""
    occurred_on: date | None = None


def _require_manage(membership: BusinessMembership | None) -> None:
    if membership is None or not membership.has_capability(LEDGER_MANAGE):
        raise LedgerError("اجازه ثبت سند مالی را ندارید.")


def _quantize(value) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise LedgerError("مبلغ واردشده معتبر نیست.") from exc


def _last_balance(business: Business, contact: Contact) -> Decimal:
    last = (
        LedgerEntry.objects.filter(business=business, contact=contact)
        .order_by("-created_at")
        .first()
    )
    return last.balance_after if last else ZERO


@transaction.atomic
def post_entry(
    *,
    business: Business,
    contact: Contact,
    membership: BusinessMembership,
    entry_type: str,
    amount,
    description: str = "",
    reference: str = "",
    occurred_on: date | None = None,
    related_lot=None,
    related_reservation=None,
) -> LedgerEntry:
    _require_manage(membership)
    if membership.business_id != business.id:
        raise LedgerError("دسترسی نامعتبر است.")
    if contact.business_id != business.id:
        raise LedgerError("این مخاطب متعلق به کسب‌وکار شما نیست.")

    if entry_type not in DIRECTION:
        raise LedgerError("نوع سند نامعتبر است.")

    amount = _quantize(amount)
    if amount <= 0:
        raise LedgerError("مبلغ باید بزرگ‌تر از صفر باشد.")

    description = (description or "").strip()
    if entry_type in ADJUSTMENT_TYPES and not description:
        raise LedgerError("برای اصلاح دستی، ذکر دلیل الزامی است.")

    if related_lot is not None and related_lot.business_id != business.id:
        raise LedgerError("محموله انتخاب‌شده متعلق به کسب‌وکار شما نیست.")
    if related_reservation is not None and business.id not in {
        related_reservation.seller_business_id,
        related_reservation.requester_business_id,
    }:
        raise LedgerError("رزرو انتخاب‌شده به کسب‌وکار شما مرتبط نیست.")

    # Serialize concurrent posts for this contact by locking the contact row.
    locked_contact = Contact.objects.select_for_update().get(pk=contact.pk, business=business)
    previous = _last_balance(business, locked_contact)
    delta = amount * DIRECTION[entry_type]
    balance_after = (previous + delta).quantize(Decimal("0.01"))

    entry = LedgerEntry.objects.create(
        business=business,
        contact=locked_contact,
        entry_type=entry_type,
        amount=amount,
        balance_delta=delta,
        balance_after=balance_after,
        description=description,
        reference=(reference or "").strip(),
        occurred_on=occurred_on or timezone.localdate(),
        related_lot=related_lot,
        related_reservation=related_reservation,
        created_by=membership.user,
    )
    logger.info(
        "Ledger entry posted id=%s business=%s contact=%s type=%s amount=%s balance_after=%s",
        entry.id,
        business.id,
        contact.id,
        entry_type,
        amount,
        balance_after,
    )
    return entry


def _format_quantity(quantity) -> str:
    """Trim the stored 3-decimal quantity for display (100.000 → 100)."""
    try:
        return format(Decimal(str(quantity)).normalize(), "f")
    except (InvalidOperation, TypeError, ValueError):
        return str(quantity)


@transaction.atomic
def post_trade_entry(
    *,
    reservation: Reservation,
    business: Business,
    contact: Contact,
    membership: BusinessMembership,
    amount,
    description: str = "",
    reference: str = "",
    occurred_on: date | None = None,
) -> LedgerEntry:
    """Record the financial result of a converted trade as one ``SALE`` entry.

    Seller side only in this phase: the buyer-side ``PURCHASE`` mirror is
    postponed (see docs/accounting.md), which is why the constraint below is
    scoped by business rather than by reservation alone.

    Idempotency has three layers: the pre-check runs under the contact row lock
    taken here, the ``uniq_trade_entry_per_reservation`` DB constraint catches
    races on a *different* contact of the same business, and both surface as
    ``LedgerDuplicateError`` so callers can report «قبلاً ثبت شده».

    Both layers ignore trades that have been reversed: ``trade_entry_for_reservation``
    filters on ``reversed_at__isnull=True`` and the constraint carries the same
    condition, so a wrong amount can be reversed and then re-recorded with the
    ``related_reservation`` link intact.
    """
    _require_manage(membership)
    if membership.business_id != business.id:
        raise LedgerError("دسترسی نامعتبر است.")
    if reservation.seller_business_id != business.id:
        raise LedgerError("فقط فروشنده می‌تواند سند مالی این معامله را ثبت کند.")
    if contact.business_id != business.id:
        raise LedgerError("این مخاطب متعلق به کسب‌وکار شما نیست.")
    if reservation.status != Reservation.Status.CONVERTED:
        raise LedgerError("تا وقتی رزرو به فروش تبدیل نشده، ثبت سند مالی ممکن نیست.")

    # Take the contact row lock before the duplicate check so the check and the
    # post below are one serialized section for this contact.
    locked_contact = Contact.objects.select_for_update().get(pk=contact.pk, business=business)
    existing = trade_entry_for_reservation(business, reservation)
    if existing is not None:
        logger.info(
            "Trade ledger entry already recorded business=%s reservation=%s entry=%s",
            business.id,
            reservation.id,
            existing.id,
        )
        raise LedgerDuplicateError(existing=existing)

    description = (description or "").strip() or (
        f"فروش از محموله {reservation.lot.lot_code} · "
        f"{_format_quantity(reservation.quantity_sqm)} مترمربع"
    )

    try:
        # Savepoint: two different contacts of this business are not serialized by
        # the row lock above, so the unique constraint can still fire. Rolling back
        # to the savepoint keeps the surrounding transaction usable.
        with transaction.atomic():
            entry = post_entry(
                business=business,
                contact=locked_contact,
                membership=membership,
                entry_type=LedgerEntry.Type.SALE,
                amount=amount,
                description=description,
                reference=reference,
                occurred_on=occurred_on,
                related_lot=reservation.lot,
                related_reservation=reservation,
            )
    except IntegrityError as exc:
        logger.warning(
            "Duplicate trade ledger entry blocked by constraint business=%s reservation=%s",
            business.id,
            reservation.id,
        )
        raise LedgerDuplicateError() from exc

    logger.info(
        "Trade ledger entry recorded id=%s business=%s reservation=%s contact=%s amount=%s",
        entry.id,
        business.id,
        reservation.id,
        locked_contact.id,
        entry.amount,
    )
    return entry


@transaction.atomic
def reverse_entry(*, entry: LedgerEntry, membership: BusinessMembership) -> LedgerEntry:
    """Post a reversal entry that negates ``entry``. Corrections never edit the
    original. Prevents reversing a reversal and prevents double reversal.

    The original is also stamped with ``reversed_at``. That stamp is a bookkeeping
    flag rather than financial data — no amount, delta, or balance changes — and is
    the one deliberate carve-out from the model's immutability: it is written with a
    queryset ``.update()`` because ``LedgerEntry.save()`` blocks updates and must
    keep doing so. It releases the ``uniq_trade_entry_per_reservation`` slot so a
    reversed trade can be re-recorded.
    """
    _require_manage(membership)
    if membership.business_id != entry.business_id:
        raise LedgerError("دسترسی نامعتبر است.")
    if entry.entry_type == LedgerEntry.Type.REVERSAL:
        raise LedgerError("یک سند برگشتی را نمی‌توان دوباره برگشت زد.")

    # Lock the contact row, then re-check under the lock for an existing reversal.
    locked_contact = Contact.objects.select_for_update().get(
        pk=entry.contact_id, business=entry.business_id
    )
    if LedgerEntry.objects.filter(reverses=entry).exists():
        raise LedgerError("این سند قبلاً برگشت خورده است.")

    previous = _last_balance(entry.business, locked_contact)
    delta = -entry.balance_delta
    balance_after = (previous + delta).quantize(Decimal("0.01"))

    reversed_at = timezone.now()
    # Same transaction and same contact row lock as the reversal below, so an
    # entry is never observed as reversed without its reversal, or vice versa.
    LedgerEntry.objects.filter(pk=entry.pk, reversed_at__isnull=True).update(
        reversed_at=reversed_at
    )
    entry.reversed_at = reversed_at

    original_label = entry.get_entry_type_display()
    reversal = LedgerEntry.objects.create(
        business_id=entry.business_id,
        contact=locked_contact,
        entry_type=LedgerEntry.Type.REVERSAL,
        amount=entry.amount,
        balance_delta=delta,
        balance_after=balance_after,
        currency=entry.currency,
        description=f"برگشت سند «{original_label}» مورخ {entry.occurred_on}",
        reference=entry.reference,
        occurred_on=timezone.localdate(),
        reverses=entry,
        created_by=membership.user,
    )
    logger.info(
        "Ledger entry reversed original=%s reversal=%s business=%s contact=%s",
        entry.id,
        reversal.id,
        entry.business_id,
        entry.contact_id,
    )
    return reversal
