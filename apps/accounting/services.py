from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.businesses.entitlements import MANAGE_LEDGER, EntitlementError, require_entitlement
from apps.businesses.models import Business, BusinessMembership
from apps.businesses.permissions import LEDGER_MANAGE, SALE_FINALIZE

from .models import TRADE_ENTRY_TYPES, LedgerEntry

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")

TRADE_ALREADY_RECORDED = "سند مالی این معامله قبلاً ثبت شده است."

# Effect of each entry type on the balance (owning-business perspective):
# +1 increases what the counterparty owes us; -1 decreases it.
DIRECTION: dict[str, int] = {
    LedgerEntry.Type.SALE: +1,
    LedgerEntry.Type.PAYMENT_MADE: +1,
    LedgerEntry.Type.ADJUST_DEBIT: +1,
    LedgerEntry.Type.PURCHASE: -1,
    LedgerEntry.Type.PAYMENT_RECEIVED: -1,
    LedgerEntry.Type.ADJUST_CREDIT: -1,
}

ADJUSTMENT_TYPES = {LedgerEntry.Type.ADJUST_DEBIT, LedgerEntry.Type.ADJUST_CREDIT}

#: The only manual entries a user may post. Deliberately four: money in, money
#: out, and a correction in each direction. Cheques, instalments and bank
#: reconciliation are explicitly out of scope — a cheque is described in the
#: reference field if the user wants to remember it.
MANUAL_ENTRY_TYPES: tuple[str, ...] = (
    LedgerEntry.Type.PAYMENT_RECEIVED,
    LedgerEntry.Type.PAYMENT_MADE,
    LedgerEntry.Type.ADJUST_DEBIT,
    LedgerEntry.Type.ADJUST_CREDIT,
)


class LedgerError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class LedgerDuplicateError(LedgerError):
    """The trade was already recorded; the caller should treat this as a no-op.

    A subclass of ``LedgerError`` so existing handlers keep working, but distinct
    so the UI can say «قبلاً ثبت شده» instead of showing a failure.
    """

    def __init__(self, message: str = TRADE_ALREADY_RECORDED, *, existing: LedgerEntry | None = None) -> None:
        self.existing = existing
        super().__init__(message)


def _require_manage(membership: BusinessMembership | None) -> None:
    if membership is None or not membership.has_capability(LEDGER_MANAGE):
        raise LedgerError("اجازه ثبت سند مالی را ندارید.")
    try:
        require_entitlement(membership.business, MANAGE_LEDGER)
    except EntitlementError as exc:
        raise LedgerError(exc.message) from exc


def _quantize(value) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise LedgerError("مبلغ واردشده معتبر نیست.") from exc


def _lock_counterparty(counterparty: Business) -> Business:
    """Serialize concurrent posts against one colleague's account.

    Locking the counterparty's Business row is coarse — it also serializes a
    different business posting to the same colleague — but it is the only row
    that uniquely identifies the account, and holding it for the length of one
    ledger post costs nothing in practice. Only one row is ever locked per
    transaction, so there is no lock-ordering deadlock to reason about.
    """
    return Business.objects.select_for_update().get(pk=counterparty.pk)


def _last_balance(business: Business, counterparty: Business) -> Decimal:
    last = (
        LedgerEntry.objects.filter(business=business, counterparty_business=counterparty)
        .order_by("-created_at")
        .first()
    )
    return last.balance_after if last else ZERO


def _validate_counterparty(business: Business, counterparty: Business) -> None:
    if counterparty is None:
        raise LedgerError("همکار را انتخاب کنید.")
    if counterparty.id == business.id:
        raise LedgerError("نمی‌توانید برای کسب‌وکار خودتان سند ثبت کنید.")


@transaction.atomic
def post_entry(
    *,
    business: Business,
    counterparty: Business,
    membership: BusinessMembership,
    entry_type: str,
    amount,
    description: str = "",
    reference: str = "",
    occurred_on: date | None = None,
    related_lot=None,
    related_trade=None,
) -> LedgerEntry:
    """Post one immutable entry that the user is authoring by hand."""
    _require_manage(membership)
    return _post(
        business=business,
        counterparty=counterparty,
        membership=membership,
        entry_type=entry_type,
        amount=amount,
        description=description,
        reference=reference,
        occurred_on=occurred_on,
        related_lot=related_lot,
        related_trade=related_trade,
    )


@transaction.atomic
def _post(
    *,
    business: Business,
    counterparty: Business,
    membership: BusinessMembership,
    entry_type: str,
    amount,
    description: str = "",
    reference: str = "",
    occurred_on: date | None = None,
    related_lot=None,
    related_trade=None,
) -> LedgerEntry:
    """Write one entry. Callers own the authorization decision.

    Split from :func:`post_entry` because the two ways money reaches the books
    are authorized differently. A manual entry is bookkeeping and needs
    ``ledger.manage``. A sale's entry is a *consequence* of finalizing that sale,
    which a salesperson holding ``sale.finalize`` is allowed to do — requiring
    ``ledger.manage`` there would mean no salesperson could complete a sale.
    """
    if membership.business_id != business.id:
        raise LedgerError("دسترسی نامعتبر است.")
    _validate_counterparty(business, counterparty)

    if entry_type not in DIRECTION:
        raise LedgerError("نوع سند نامعتبر است.")

    amount = _quantize(amount)
    if amount <= 0:
        raise LedgerError("مبلغ باید بزرگ‌تر از صفر باشد.")

    description = (description or "").strip()
    if entry_type in ADJUSTMENT_TYPES and not description:
        raise LedgerError("برای اصلاح دستی، ذکر دلیل الزامی است.")

    if related_lot is not None and related_lot.business_id != business.id:
        raise LedgerError("محصول انتخاب‌شده متعلق به کسب‌وکار شما نیست.")
    if related_trade is not None and business.id not in {
        related_trade.seller_business_id,
        related_trade.buyer_business_id,
    }:
        raise LedgerError("این معامله به کسب‌وکار شما مرتبط نیست.")

    locked = _lock_counterparty(counterparty)
    previous = _last_balance(business, locked)
    delta = amount * DIRECTION[entry_type]
    balance_after = (previous + delta).quantize(Decimal("0.01"))

    entry = LedgerEntry.objects.create(
        business=business,
        counterparty_business=locked,
        legacy_counterparty_name=locked.name,
        entry_type=entry_type,
        amount=amount,
        balance_delta=delta,
        balance_after=balance_after,
        description=description,
        reference=(reference or "").strip(),
        occurred_on=occurred_on or timezone.localdate(),
        related_lot=related_lot,
        related_trade=related_trade,
        created_by=membership.user,
    )
    logger.info(
        "Ledger entry posted id=%s business=%s counterparty=%s type=%s amount=%s balance_after=%s",
        entry.id,
        business.id,
        locked.id,
        entry_type,
        amount,
        balance_after,
    )
    return entry


def post_manual_entry(
    *,
    business: Business,
    counterparty: Business,
    membership: BusinessMembership,
    entry_type: str,
    amount,
    description: str = "",
    reference: str = "",
    occurred_on: date | None = None,
) -> LedgerEntry:
    """The user-facing entry point: دریافت، پرداخت، اصلاح بدهکار، اصلاح بستانکار.

    Refuses trade types outright, so a sale can only ever reach the books
    through :func:`post_trade_for_sale` — one authoritative event, not two ways in.
    """
    if entry_type not in MANUAL_ENTRY_TYPES:
        raise LedgerError("این نوع سند به‌صورت دستی قابل ثبت نیست.")
    return post_entry(
        business=business,
        counterparty=counterparty,
        membership=membership,
        entry_type=entry_type,
        amount=amount,
        description=description,
        reference=reference,
        occurred_on=occurred_on,
    )


def _format_quantity(quantity) -> str:
    """Trim the stored 3-decimal quantity for display (100.000 → 100)."""
    try:
        return format(Decimal(str(quantity)).normalize(), "f")
    except (InvalidOperation, TypeError, ValueError):
        return str(quantity)


def _trade_description(trade) -> str:
    return f"فروش {trade.product_name} · {_format_quantity(trade.quantity_sqm)} مترمربع"


@transaction.atomic
def post_trade_for_sale(*, trade, membership: BusinessMembership) -> LedgerEntry | None:
    """Post the seller's ledger entry for a finalized Trade.

    This is the **one** authoritative financial event of a sale. Issuing or
    printing the invoice afterwards must never post again, which is why the
    invoice code calls nothing here.

    Returns ``None`` for a sale to a walk-in customer: there is no colleague
    account to move, and inventing one would create a debtor nobody can settle
    with.

    Exactly-once has three layers, the same shape the offer-linked path has used
    since v1: the pre-check runs under the counterparty row lock taken here, the
    ``uniq_trade_entry_per_trade`` constraint catches anything that slips past,
    and both surface as :class:`LedgerDuplicateError`.
    """
    if trade.buyer_business_id is None:
        logger.info("Trade %s is a direct customer sale; no colleague ledger entry", trade.id)
        return None

    business = trade.seller_business
    # Authorized by SALE_FINALIZE, not LEDGER_MANAGE: this entry is a consequence
    # of the sale the user just completed, not bookkeeping they are authoring.
    if membership is None or not membership.has_capability(SALE_FINALIZE):
        raise LedgerError("اجازه نهایی کردن فروش را ندارید.")
    if membership.business_id != business.id:
        raise LedgerError("دسترسی نامعتبر است.")

    locked = _lock_counterparty(trade.buyer_business)
    existing = LedgerEntry.objects.filter(
        business=business,
        related_trade=trade,
        entry_type__in=TRADE_ENTRY_TYPES,
        reversed_at__isnull=True,
    ).first()
    if existing is not None:
        raise LedgerDuplicateError(existing=existing)

    try:
        # Savepoint so a constraint violation leaves the surrounding transaction
        # usable instead of poisoning the whole finalization.
        with transaction.atomic():
            entry = _post(
                business=business,
                counterparty=locked,
                membership=membership,
                entry_type=LedgerEntry.Type.SALE,
                amount=trade.total_amount,
                description=_trade_description(trade),
                occurred_on=timezone.localdate(),
                related_lot=trade.item,
                related_trade=trade,
            )
    except IntegrityError as exc:
        logger.warning("Duplicate trade ledger entry blocked by constraint trade=%s", trade.id)
        raise LedgerDuplicateError() from exc

    return entry


@transaction.atomic
def reverse_entry(*, entry: LedgerEntry, membership: BusinessMembership) -> LedgerEntry:
    """Post a reversal entry that negates ``entry``.

    Corrections never edit the original. The original is stamped with
    ``reversed_at``, which is a bookkeeping flag rather than financial data — no
    amount, delta or balance changes. That stamp is the one deliberate carve-out
    from immutability, written with a queryset ``.update()`` because
    ``LedgerEntry.save()`` blocks updates and must keep doing so. It releases the
    idempotency slot so a corrected trade can be re-recorded.
    """
    _require_manage(membership)
    if membership.business_id != entry.business_id:
        raise LedgerError("دسترسی نامعتبر است.")
    if entry.entry_type == LedgerEntry.Type.REVERSAL:
        raise LedgerError("یک سند برگشتی را نمی‌توان دوباره برگشت زد.")
    if entry.counterparty_business_id is None:
        # Pre-V2 rows with no mapped Business are read-only history; there is no
        # account to post the correction against.
        raise LedgerError("این سند قدیمی قابل برگشت نیست.")

    locked = _lock_counterparty(entry.counterparty_business)
    if LedgerEntry.objects.filter(reverses=entry).exists():
        raise LedgerError("این سند قبلاً برگشت خورده است.")

    previous = _last_balance(entry.business, locked)
    delta = -entry.balance_delta
    balance_after = (previous + delta).quantize(Decimal("0.01"))

    reversed_at = timezone.now()
    # Same transaction and same row lock as the reversal below, so an entry is
    # never observed as reversed without its reversal, or vice versa.
    LedgerEntry.objects.filter(pk=entry.pk, reversed_at__isnull=True).update(reversed_at=reversed_at)
    entry.reversed_at = reversed_at

    original_label = entry.get_entry_type_display()
    reversal = LedgerEntry.objects.create(
        business_id=entry.business_id,
        counterparty_business=locked,
        legacy_counterparty_name=locked.name,
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
        "Ledger entry reversed original=%s reversal=%s business=%s counterparty=%s",
        entry.id,
        reversal.id,
        entry.business_id,
        locked.id,
    )
    return reversal
