"""Issuing invoices.

Two rules shape this module.

**An invoice never posts to the ledger.** The books move exactly once, when a
sale is finalized. Issuing or printing the invoice afterwards is a document
operation. Wiring a second posting point here is how a business ends up with
every sale counted twice.

**Numbers are allocated under a lock.** ``count() + 1`` looks obviously correct
and produces duplicate invoice numbers the first time two salespeople issue at
the same moment.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from apps.businesses.entitlements import ISSUE_INVOICES, EntitlementError, require_entitlement
from apps.businesses.models import Business, BusinessMembership
from apps.businesses.permissions import INVOICE_MANAGE

from .models import SalesInvoice, SalesInvoiceItem

logger = logging.getLogger(__name__)


class InvoiceError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def _require_manage(business: Business, membership: BusinessMembership) -> None:
    """For invoices a user is authoring: they need ``invoice.manage``."""
    if membership is None or not membership.has_capability(INVOICE_MANAGE):
        raise InvoiceError("اجازه صدور فاکتور را ندارید.")
    _require_seller_may_invoice(business, membership)


def _require_seller_may_invoice(business: Business, membership: BusinessMembership) -> None:
    """For invoices that are a *consequence* of a sale, not an authored document.

    Deliberately no ``invoice.manage`` check. The default sales role can finalize
    a sale but not manage invoices, so requiring it here meant a staff member
    completed a sale, the ledger moved, and the invoice was silently swallowed —
    leaving a finalized trade with no document and no way to ask for one. The
    invoice is not a second commercial decision; it records the one already made.

    The Business still has to be entitled to issue invoices at all.
    """
    if membership is None:
        raise InvoiceError("دسترسی نامعتبر است.")
    if membership.business_id != business.id:
        raise InvoiceError("دسترسی نامعتبر است.")
    try:
        require_entitlement(business, ISSUE_INVOICES)
    except EntitlementError as exc:
        raise InvoiceError(exc.message) from exc


def _quantize(value, places: str = "0.01") -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal(places))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise InvoiceError("مقدار واردشده معتبر نیست.") from exc


def allocate_number(business: Business) -> str:
    """Next invoice number for this seller.

    Called inside the caller's transaction with the seller's Business row
    already locked, so the read-then-write below cannot interleave with another
    allocation. Derived from MAX rather than COUNT so cancelling an invoice
    never causes a number to be reused.
    """
    highest = (
        SalesInvoice.objects.filter(seller_business=business)
        .annotate(numeric=Max("number"))
        .values_list("number", flat=True)
    )
    largest = 0
    for number in highest:
        try:
            largest = max(largest, int(str(number).split("-")[-1]))
        except (TypeError, ValueError):
            continue
    return f"{largest + 1:05d}"


@transaction.atomic
def create_invoice_for_trade(
    *,
    trade,
    membership: BusinessMembership,
    notes: str = "",
    issue: bool | None = None,
) -> SalesInvoice:
    """Turn a finalized Trade into an invoice.

    Idempotent three ways, because a lookup alone is not: the seller row is
    locked *before* the existence check so concurrent callers serialize, the
    check runs again under that lock, and ``uniq_invoice_per_trade`` catches
    anything that still slips past — in which case the loser returns the
    winner's document instead of failing.

    Nothing here touches the ledger; that already happened when the trade was
    finalized.

    ``issue`` defaults to whether the acting member may issue documents. Someone
    who can sell but not manage invoices still gets one — as a draft, for an
    authorized colleague to issue — rather than nothing at all.
    """
    business = trade.seller_business
    _require_seller_may_invoice(business, membership)
    if issue is None:
        issue = membership.has_capability(INVOICE_MANAGE)

    # Lock first, then look. Checking before the lock is what allowed two
    # requests to both conclude "no invoice yet" and both create one.
    Business.objects.select_for_update().get(pk=business.pk)

    existing = SalesInvoice.objects.filter(trade=trade).first()
    if existing is not None:
        return existing

    try:
        with transaction.atomic():
            invoice = SalesInvoice.objects.create(
                seller_business=business,
                number=allocate_number(business),
                counterparty_type=(
                    SalesInvoice.Counterparty.BUSINESS
                    if trade.buyer_business_id
                    else SalesInvoice.Counterparty.CUSTOMER
                ),
                buyer_business=trade.buyer_business,
                customer_name=trade.customer_name,
                customer_phone=trade.customer_phone,
                buyer_name=trade.counterparty_label,
                trade=trade,
                issue_date=timezone.localdate(),
                status=SalesInvoice.Status.ISSUED if issue else SalesInvoice.Status.DRAFT,
                total_amount=trade.total_amount,
                currency=trade.currency,
                notes=(notes or "").strip(),
                created_by=membership.user,
            )
            SalesInvoiceItem.objects.create(
                invoice=invoice,
                item=trade.item,
                product_name=trade.product_name,
                stone_type=trade.stone_type,
                grade=trade.grade,
                quantity=trade.quantity_sqm,
                unit_price=trade.unit_price,
                line_total=trade.total_amount,
            )
    except IntegrityError:
        winner = SalesInvoice.objects.filter(trade=trade).first()
        if winner is None:
            raise
        logger.info("Concurrent invoice creation for trade %s resolved to %s", trade.id, winner.number)
        return winner

    logger.info("Invoice %s created for trade %s", invoice.number, trade.id)
    return invoice


@transaction.atomic
def create_manual_invoice(
    *,
    business: Business,
    membership: BusinessMembership,
    lines: list[dict],
    buyer_business: Business | None = None,
    customer_name: str = "",
    customer_phone: str = "",
    notes: str = "",
    issue_date=None,
    issue: bool = True,
) -> SalesInvoice:
    """An invoice typed by hand for a walk-in customer.

    Each line carries its own snapshot. ``item`` may be supplied for navigation,
    but the name, grade and price stored on the line are what the document shows
    forever after.

    A **colleague** invoice cannot be created here. A sale to another Business
    moves that colleague's account, and the only thing allowed to move an account
    is a finalized Trade. Letting this path issue a colleague invoice produced a
    valid-looking document while the colleague's balance stayed untouched — one
    commercial event with two representations, only one of which counted. Use
    :func:`apps.trading.services.record_direct_sale` instead; it creates the
    Trade, posts both books and returns the invoice.
    """
    _require_manage(business, membership)

    if buyer_business is not None:
        raise InvoiceError(
            "فروش به همکار باید از «ثبت فروش مستقیم» ثبت شود تا حساب همکار هم به‌روز شود."
        )

    counterparty_type = SalesInvoice.Counterparty.CUSTOMER
    customer_name = (customer_name or "").strip()
    if not customer_name:
        raise InvoiceError("نام خریدار را وارد کنید.")
    buyer_name = customer_name

    cleaned = [_clean_line(line) for line in (lines or []) if line]
    if not cleaned:
        raise InvoiceError("حداقل یک ردیف به فاکتور اضافه کنید.")

    Business.objects.select_for_update().get(pk=business.pk)

    invoice = SalesInvoice.objects.create(
        seller_business=business,
        number=allocate_number(business),
        counterparty_type=counterparty_type,
        buyer_business=None,
        customer_name=customer_name,
        customer_phone=(customer_phone or "").strip(),
        buyer_name=buyer_name,
        issue_date=issue_date or timezone.localdate(),
        status=SalesInvoice.Status.ISSUED if issue else SalesInvoice.Status.DRAFT,
        total_amount=sum((line["line_total"] for line in cleaned), Decimal("0")),
        notes=(notes or "").strip(),
        created_by=membership.user,
    )
    SalesInvoiceItem.objects.bulk_create(
        [
            SalesInvoiceItem(
                invoice=invoice,
                item=line["item"],
                product_name=line["product_name"],
                stone_type=line["stone_type"],
                grade=line["grade"],
                quantity=line["quantity"],
                unit_price=line["unit_price"],
                line_total=line["line_total"],
                sort_order=index,
            )
            for index, line in enumerate(cleaned)
        ]
    )
    return invoice


def _clean_line(line: dict) -> dict:
    item = line.get("item")
    name = (line.get("product_name") or "").strip()
    if not name and item is not None:
        name = item.product.commercial_name
    if not name:
        raise InvoiceError("نام محصول هر ردیف را وارد کنید.")

    quantity = _quantize(line.get("quantity"), "0.001")
    if quantity <= 0:
        raise InvoiceError("مقدار هر ردیف باید بزرگ‌تر از صفر باشد.")
    unit_price = _quantize(line.get("unit_price"))
    if unit_price < 0:
        raise InvoiceError("قیمت نمی‌تواند منفی باشد.")

    return {
        "item": item,
        "product_name": name,
        "stone_type": (line.get("stone_type") or (item.product.stone_type if item else "")),
        "grade": (line.get("grade") or (item.grade if item else "")),
        "quantity": quantity,
        "unit_price": unit_price,
        "line_total": (quantity * unit_price).quantize(Decimal("0.01")),
    }


@transaction.atomic
def issue_invoice(*, invoice: SalesInvoice, membership: BusinessMembership) -> SalesInvoice:
    """Move a draft to issued.

    Explicitly does **not** post to the ledger. The sale already did that when it
    was finalized; posting here would double every sale in the books.
    """
    _require_manage(invoice.seller_business, membership)
    if invoice.status == SalesInvoice.Status.ISSUED:
        return invoice
    if invoice.status == SalesInvoice.Status.CANCELLED:
        raise InvoiceError("فاکتور باطل‌شده قابل صدور نیست.")

    invoice.status = SalesInvoice.Status.ISSUED
    invoice.save(update_fields=["status", "updated_at"])
    return invoice


@transaction.atomic
def cancel_invoice(*, invoice: SalesInvoice, membership: BusinessMembership) -> SalesInvoice:
    """Void the document without deleting it or reusing its number.

    Cancelling changes no balance: if the sale itself was wrong, the ledger entry
    is reversed separately. Keeping the two apart is what stops "I fixed the
    invoice" from quietly meaning "I moved money".
    """
    _require_manage(invoice.seller_business, membership)
    invoice.status = SalesInvoice.Status.CANCELLED
    invoice.save(update_fields=["status", "updated_at"])
    return invoice


def safe_create_invoice_for_trade(*, trade, membership: BusinessMembership) -> SalesInvoice | None:
    """Best-effort invoice creation from the sale-finalization flow.

    Returns ``None`` instead of raising when the business is not entitled to
    issue invoices or a race produced the row first: failing to create a
    convenience document must never roll back a completed sale.
    """
    try:
        return create_invoice_for_trade(trade=trade, membership=membership)
    except (InvoiceError, IntegrityError):
        logger.info("Invoice not created for trade %s", trade.id, exc_info=True)
        return None
