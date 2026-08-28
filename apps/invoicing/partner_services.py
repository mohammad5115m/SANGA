"""Transactional invoice-first partner commerce and settlement commands."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.businesses.eligibility import business_is_network_eligible
from apps.businesses.models import Business, BusinessMembership
from apps.businesses.permissions import (
    CHEQUE_MANAGE,
    COUNTERPARTY_LINK_APPROVE,
    COUNTERPARTY_LINK_PROPOSE,
    INVOICE_CONFIRM,
    INVOICE_OFFLINE_APPROVE,
    INVOICE_SEND,
    LOCAL_COUNTERPARTY_MANAGE,
)
from apps.notifications.services import notify_business
from apps.trading.models import Trade, TradeItem

from .models import (
    BusinessInvoiceSettings,
    ChequeEvent,
    ChequeReceivable,
    CounterpartyLinkProposal,
    InvoiceRevision,
    LocalCounterparty,
    SalesInvoice,
    SettlementEvent,
    UserInvoiceSignature,
    normalize_counterparty_name,
    normalize_phone,
)
from .services import (
    InvoiceError,
    _assign_totals,
    _calculate,
    _clean_lines,
    _replace_items,
    _require_manage,
    allocate_number,
    appearance_snapshot,
    get_invoice_settings,
    seller_snapshot,
)

CENT = Decimal("0.01")


def _money(value) -> Decimal:
    try:
        return Decimal(str(value or 0)).quantize(CENT)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise InvoiceError("مبلغ تسویه معتبر نیست.") from exc


def _require(membership: BusinessMembership | None, capability: str, message: str) -> None:
    if membership is None or not membership.has_capability(capability):
        raise InvoiceError(message)


def resolve_local_counterparty(
    *,
    business: Business,
    membership: BusinessMembership,
    local_counterparty: LocalCounterparty | None = None,
    name: str = "",
    phone: str = "",
    address: str = "",
) -> LocalCounterparty:
    _require(
        membership,
        LOCAL_COUNTERPARTY_MANAGE,
        "اجازه مدیریت همکاران محلی را ندارید.",
    )
    if membership.business_id != business.id:
        raise InvoiceError("دسترسی نامعتبر است.")
    if local_counterparty is not None:
        if local_counterparty.owner_business_id != business.id:
            raise InvoiceError("همکار محلی متعلق به این کسب‌وکار نیست.")
        return local_counterparty
    name = str(name or "").strip()
    if not name:
        raise InvoiceError("نام همکار محلی را وارد کنید.")
    # Similar rows are surfaced by selectors/UI; they are deliberately not merged.
    return LocalCounterparty.objects.create(
        owner_business=business,
        name=name,
        phone=str(phone or "").strip(),
        address=str(address or "").strip(),
        created_by=membership.user,
    )


def likely_local_duplicates(*, business: Business, name: str = "", phone: str = ""):
    from django.db.models import Q

    query = Q()
    normalized_name = normalize_counterparty_name(name)
    normalized_phone = normalize_phone(phone)
    if normalized_name:
        query |= Q(normalized_name__icontains=normalized_name)
    if normalized_phone:
        query |= Q(normalized_phone=normalized_phone)
    if not query:
        return LocalCounterparty.objects.none()
    return LocalCounterparty.objects.filter(owner_business=business, status=LocalCounterparty.Status.ACTIVE).filter(
        query
    )[:8]


def _validate_settlement(invoice: SalesInvoice) -> None:
    cash = _money(invoice.cash_amount)
    credit = _money(invoice.credit_amount)
    cheque = _money(invoice.cheque_amount)
    if any(value < 0 for value in (cash, credit, cheque)):
        raise InvoiceError("مبالغ تسویه نمی‌توانند منفی باشند.")
    if cash + credit + cheque != _money(invoice.total_amount):
        raise InvoiceError("جمع نقد، اعتبار و چک باید دقیقاً با مبلغ نهایی فاکتور برابر باشد.")
    expected = {
        SalesInvoice.SettlementMethod.CASH: (cash > 0 and not credit and not cheque),
        SalesInvoice.SettlementMethod.CREDIT: (credit > 0 and not cash and not cheque),
        SalesInvoice.SettlementMethod.CHEQUE: (cheque > 0 and not cash and not credit),
        SalesInvoice.SettlementMethod.MIXED: sum(bool(v) for v in (cash, credit, cheque)) >= 2,
    }
    if not expected.get(invoice.settlement_method, False):
        raise InvoiceError("روش تسویه با مبالغ تخصیص‌یافته سازگار نیست.")
    if cheque:
        details = invoice.cheque_details or {}
        if not str(details.get("reference_number", "")).strip() or not details.get("due_date"):
            raise InvoiceError("برای بخش چک، شماره چک و تاریخ سررسید الزامی است.")


def create_partner_draft(
    *,
    business: Business,
    membership: BusinessMembership,
    lines: list[dict],
    buyer_business: Business | None = None,
    local_counterparty: LocalCounterparty | None = None,
    buyer_name: str = "",
    buyer_phone: str = "",
    buyer_address: str = "",
    issue_date=None,
    currency: str = "IRR",
    display_unit: str = "IRR",
    invoice_discount_type: str = "none",
    invoice_discount_value=0,
    tax_amount=0,
    shipping_amount=0,
    adjustment_amount=0,
    settlement_method: str = SalesInvoice.SettlementMethod.CREDIT,
    cash_amount=0,
    credit_amount=0,
    cheque_amount=0,
    cheque_details: dict | None = None,
    notes: str = "",
    payment_terms: str = "",
    submission_id: uuid.UUID | None = None,
) -> SalesInvoice:
    _require_manage(business, membership)
    if (buyer_business is None) == (local_counterparty is None):
        raise InvoiceError("یک همکار ثبت‌شده یا محلی انتخاب کنید.")
    if buyer_business is not None and buyer_business.id == business.id:
        raise InvoiceError("نمی‌توانید برای کسب‌وکار خودتان فاکتور بفرستید.")
    if local_counterparty is not None and local_counterparty.owner_business_id != business.id:
        raise InvoiceError("همکار محلی متعلق به این کسب‌وکار نیست.")
    if submission_id:
        existing = SalesInvoice.objects.filter(seller_business=business, submission_id=submission_id).first()
        if existing:
            return existing
    cleaned = _clean_lines(
        lines,
        seller_business=business,
        currency=currency,
        display_unit=display_unit,
        values_are_display=True,
    )
    paid = _money(cash_amount) + _money(cheque_amount)
    calculated, totals = _calculate(
        cleaned,
        currency=currency,
        display_unit=display_unit,
        values_are_display=True,
        invoice_discount_type=invoice_discount_type,
        invoice_discount_value=invoice_discount_value,
        tax_amount=tax_amount,
        shipping_amount=shipping_amount,
        adjustment_amount=adjustment_amount,
        paid_amount=paid,
    )
    settings_row = get_invoice_settings(business)
    counterparty_name = buyer_business.name if buyer_business is not None else local_counterparty.name
    invoice = SalesInvoice(
        seller_business=business,
        submission_id=submission_id,
        counterparty_type=(
            SalesInvoice.Counterparty.BUSINESS if buyer_business is not None else SalesInvoice.Counterparty.LOCAL
        ),
        buyer_business=buyer_business,
        local_counterparty=local_counterparty,
        buyer_name=counterparty_name or str(buyer_name or "").strip(),
        buyer_phone=(buyer_business.phone if buyer_business is not None else local_counterparty.phone)
        or str(buyer_phone or "").strip(),
        buyer_address=(buyer_business.address if buyer_business is not None else local_counterparty.address)
        or str(buyer_address or "").strip(),
        issue_date=issue_date or timezone.localdate(),
        currency=currency,
        display_unit=display_unit,
        settlement_method=settlement_method,
        cash_amount=_money(cash_amount),
        credit_amount=_money(credit_amount),
        cheque_amount=_money(cheque_amount),
        cheque_details=cheque_details or {},
        notes=str(notes or "").strip(),
        payment_terms=str(payment_terms or settings_row.payment_terms).strip(),
        appearance_snapshot=appearance_snapshot(settings_row),
        created_by=membership.user,
    )
    _assign_totals(invoice, totals)
    with transaction.atomic():
        try:
            invoice.save()
            _replace_items(invoice, calculated)
        except IntegrityError:
            if not submission_id:
                raise
            return SalesInvoice.objects.get(seller_business=business, submission_id=submission_id)
    return invoice


def update_partner_draft(
    *, invoice: SalesInvoice, membership: BusinessMembership, lines: list[dict], **header
) -> SalesInvoice:
    with transaction.atomic():
        invoice = SalesInvoice.objects.select_for_update().get(pk=invoice.pk)
        _require_manage(invoice.seller_business, membership)
        if invoice.status != SalesInvoice.Status.DRAFT:
            raise InvoiceError("فقط پیش‌نویس قابل ویرایش است.")
        currency = header.get("currency", invoice.currency)
        display = header.get("display_unit", invoice.display_unit)
        cleaned = _clean_lines(
            lines,
            seller_business=invoice.seller_business,
            currency=currency,
            display_unit=display,
            values_are_display=True,
        )
        invoice.cash_amount = _money(header.get("cash_amount", invoice.cash_amount))
        invoice.credit_amount = _money(header.get("credit_amount", invoice.credit_amount))
        invoice.cheque_amount = _money(header.get("cheque_amount", invoice.cheque_amount))
        calculated, totals = _calculate(
            cleaned,
            currency=currency,
            display_unit=display,
            values_are_display=True,
            invoice_discount_type=header.get("invoice_discount_type", "none"),
            invoice_discount_value=header.get("invoice_discount_value", 0),
            tax_amount=header.get("tax_amount", 0),
            shipping_amount=header.get("shipping_amount", 0),
            adjustment_amount=header.get("adjustment_amount", 0),
            paid_amount=invoice.cash_amount + invoice.cheque_amount,
        )
        invoice.currency = currency
        invoice.display_unit = display
        invoice.issue_date = header.get("issue_date") or invoice.issue_date
        invoice.settlement_method = header.get("settlement_method", invoice.settlement_method)
        invoice.cheque_details = header.get("cheque_details") or {}
        invoice.notes = str(header.get("notes", invoice.notes) or "").strip()
        invoice.payment_terms = str(header.get("payment_terms", invoice.payment_terms) or "").strip()
        _assign_totals(invoice, totals)
        invoice.version += 1
        invoice.save()
        _replace_items(invoice, calculated)
        return invoice


def _payload(invoice: SalesInvoice) -> dict:
    return {
        "schema_version": 1,
        "invoice_id": str(invoice.id),
        "revision": invoice.current_revision_number + 1,
        "seller_business_id": str(invoice.seller_business_id),
        "counterparty_type": invoice.counterparty_type,
        "buyer_business_id": str(invoice.buyer_business_id or ""),
        "local_counterparty_id": str(invoice.local_counterparty_id or ""),
        "buyer": {
            "name": invoice.buyer_name,
            "phone": invoice.buyer_phone,
            "address": invoice.buyer_address,
        },
        "issue_date": invoice.issue_date.isoformat(),
        "currency": invoice.currency,
        "total_amount": str(invoice.total_amount),
        "settlement": {
            "method": invoice.settlement_method,
            "cash": str(invoice.cash_amount),
            "credit": str(invoice.credit_amount),
            "cheque": str(invoice.cheque_amount),
            "cheque_details": invoice.cheque_details or {},
        },
        "notes": invoice.notes,
        "lines": [
            {
                "item_id": str(line.item_id or ""),
                "product_name": line.product_name,
                "stone_type": line.stone_type,
                "grade": line.grade,
                "description": line.description,
                "quantity": str(line.quantity),
                "unit": line.unit,
                "unit_price": str(line.unit_price),
                "line_total": str(line.line_total),
            }
            for line in invoice.items.all()
        ],
    }


def _copy_asset(target, field_name: str, source, name: str) -> None:
    if not source:
        raise InvoiceError("فایل امضا یافت نشد.")
    try:
        source.open("rb")
        content = source.read()
        source.close()
    except (OSError, ValueError) as exc:
        raise InvoiceError("خواندن فایل امضا ممکن نیست.") from exc
    getattr(target, field_name).save(name, ContentFile(content), save=False)


def _freeze_revision(
    invoice: SalesInvoice,
    membership: BusinessMembership,
    *,
    require_seller_signatures: bool = True,
) -> InvoiceRevision:
    payload = _payload(invoice)
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    revision_number = invoice.current_revision_number + 1
    revision = InvoiceRevision(
        invoice=invoice,
        revision_number=revision_number,
        payload=payload,
        payload_hash=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        sent_by=membership.user,
        sent_at=timezone.now(),
    )
    if require_seller_signatures:
        settings_row = get_invoice_settings(invoice.seller_business)
        personal = UserInvoiceSignature.objects.filter(user=membership.user).first()
        if not settings_row.signature:
            raise InvoiceError("امضای رسمی کسب‌وکار ثبت نشده است؛ آن را در تنظیمات فاکتور ثبت کنید.")
        if personal is None or not personal.image:
            raise InvoiceError("امضای شخصی شما ثبت نشده است؛ ابتدا آن را در تنظیمات امضای شخصی ثبت کنید.")
        _copy_asset(revision, "seller_business_signature", settings_row.signature, "seller-business.png")
        _copy_asset(revision, "seller_user_signature", personal.image, "seller-user.png")
    revision.save()
    invoice.current_revision_number = revision_number
    return revision


def _create_trade(invoice: SalesInvoice, actor) -> Trade:
    if invoice.trade_id:
        return invoice.trade
    existing = Trade.objects.filter(seller_business=invoice.seller_business, submission_id=invoice.id).first()
    if existing:
        SalesInvoice.objects.filter(pk=invoice.pk, trade__isnull=True).update(trade=existing)
        invoice.trade = existing
        return existing
    lines = list(invoice.items.all())
    first = lines[0] if len(lines) == 1 else None
    trade = Trade.objects.create(
        submission_id=invoice.id,
        seller_business=invoice.seller_business,
        counterparty_type=invoice.counterparty_type,
        buyer_business=invoice.buyer_business,
        customer_name=(invoice.customer_name if invoice.counterparty_type == "customer" else ""),
        customer_phone=(invoice.customer_phone if invoice.counterparty_type == "customer" else ""),
        local_counterparty_id_snapshot=invoice.local_counterparty_id,
        local_counterparty_name=(invoice.local_counterparty.name if invoice.local_counterparty_id else ""),
        item=first.item if first else None,
        product_name=first.product_name if first else "",
        stone_type=first.stone_type if first else "",
        grade=first.grade if first else "",
        quantity_sqm=first.quantity if first else None,
        unit_price=first.unit_price if first else None,
        total_amount=invoice.total_amount,
        currency=invoice.currency,
        note=invoice.notes,
        finalized_at=timezone.now(),
        created_by=actor,
    )
    TradeItem.objects.bulk_create(
        [
            TradeItem(
                trade=trade,
                item=line.item,
                product_name=line.product_name,
                stone_type=line.stone_type,
                grade=line.grade,
                quantity=line.quantity,
                unit=line.unit,
                unit_price=line.unit_price,
                line_total=line.line_total,
                sort_order=line.sort_order,
            )
            for line in lines
        ]
    )
    SalesInvoice.objects.filter(pk=invoice.pk, trade__isnull=True).update(trade=trade)
    invoice.trade = trade
    return trade


def _create_settlement_events(invoice: SalesInvoice, revision: InvoiceRevision, actor) -> None:
    for kind, amount in (
        (SettlementEvent.Kind.CASH, invoice.cash_amount),
        (SettlementEvent.Kind.CREDIT, invoice.credit_amount),
        (SettlementEvent.Kind.CHEQUE, invoice.cheque_amount),
    ):
        if not amount:
            continue
        event, _ = SettlementEvent.objects.get_or_create(
            idempotency_key=f"invoice:{invoice.id}:revision:{revision.revision_number}:{kind}",
            defaults={
                "invoice": invoice,
                "revision": revision,
                "kind": kind,
                "amount": amount,
                "currency": invoice.currency,
                "recorded_by": actor,
                "occurred_at": invoice.confirmed_at or invoice.issued_at or timezone.now(),
            },
        )
        if kind == SettlementEvent.Kind.CHEQUE:
            details = invoice.cheque_details or {}
            due = details.get("due_date")
            if isinstance(due, str):
                due = date.fromisoformat(due)
            cheque, created = ChequeReceivable.objects.get_or_create(
                settlement_event=event,
                defaults={
                    "invoice": invoice,
                    "amount": amount,
                    "currency": invoice.currency,
                    "reference_number": str(details.get("reference_number", "")).strip(),
                    "bank": str(details.get("bank", "")).strip(),
                    "due_date": due,
                    "drawer_name": str(details.get("drawer_name", "")).strip(),
                },
            )
            if created:
                ChequeEvent.objects.create(
                    cheque=cheque,
                    from_status="",
                    to_status=ChequeReceivable.Status.RECEIVED,
                    idempotency_key=f"cheque:{cheque.id}:received",
                    recorded_by=actor,
                    occurred_at=timezone.now(),
                )


@transaction.atomic
def finalize_customer_invoice(*, invoice: SalesInvoice, membership: BusinessMembership) -> SalesInvoice:
    invoice = SalesInvoice.objects.select_for_update(of=("self",)).select_related("seller_business").get(pk=invoice.pk)
    _require_manage(invoice.seller_business, membership)
    if invoice.status == SalesInvoice.Status.ISSUED:
        return invoice
    if invoice.status != SalesInvoice.Status.DRAFT or invoice.counterparty_type != "customer":
        raise InvoiceError("فقط پیش‌نویس فاکتور مشتری قابل صدور است.")
    if _money(invoice.paid_amount) != _money(invoice.total_amount):
        raise InvoiceError("صدور فاکتور مشتری فقط پس از دریافت کامل مبلغ امکان‌پذیر است.")
    Business.objects.select_for_update().get(pk=invoice.seller_business_id)
    invoice.cash_amount = invoice.total_amount
    invoice.credit_amount = Decimal("0")
    invoice.cheque_amount = Decimal("0")
    invoice.settlement_method = SalesInvoice.SettlementMethod.CASH
    invoice.payment_status = SalesInvoice.PaymentStatus.PAID
    invoice.amount_due = Decimal("0")
    settings_row = get_invoice_settings(invoice.seller_business)
    invoice.number = invoice.number or allocate_number(invoice.seller_business)
    invoice.seller_snapshot = seller_snapshot(invoice.seller_business, settings_row)
    invoice.appearance_snapshot = appearance_snapshot(settings_row, invoice.appearance_snapshot)
    invoice.status = SalesInvoice.Status.ISSUED
    invoice.issued_at = timezone.now()
    invoice.issued_by = membership.user
    revision = _freeze_revision(invoice, membership, require_seller_signatures=False)
    invoice.save()
    _create_trade(invoice, membership.user)
    _create_settlement_events(invoice, revision, membership.user)
    return invoice


@transaction.atomic
def send_partner_invoice(*, invoice: SalesInvoice, membership: BusinessMembership) -> SalesInvoice:
    invoice = (
        SalesInvoice.objects.select_for_update(of=("self",))
        .select_related("seller_business", "buyer_business")
        .prefetch_related("items")
        .get(pk=invoice.pk)
    )
    _require(membership, INVOICE_SEND, "اجازه ارسال فاکتور همکار را ندارید.")
    if membership.business_id != invoice.seller_business_id:
        raise InvoiceError("فقط فروشنده می‌تواند فاکتور را ارسال کند.")
    if invoice.counterparty_type != SalesInvoice.Counterparty.BUSINESS:
        raise InvoiceError("فقط فاکتور همکار ثبت‌شده داخل سنگا ارسال می‌شود.")
    if invoice.status != SalesInvoice.Status.DRAFT:
        if invoice.status == SalesInvoice.Status.AWAITING_CONFIRMATION:
            return invoice
        raise InvoiceError("این فاکتور قابل ارسال نیست.")
    _validate_settlement(invoice)
    revision = _freeze_revision(invoice, membership)
    invoice.seller_snapshot = seller_snapshot(invoice.seller_business, get_invoice_settings(invoice.seller_business))
    invoice.status = SalesInvoice.Status.AWAITING_CONFIRMATION
    invoice.sent_at = revision.sent_at
    invoice.sent_by = membership.user
    invoice.save()
    transaction.on_commit(
        lambda buyer=invoice.buyer_business, invoice_id=invoice.id: notify_business(
            buyer,
            capability=INVOICE_CONFIRM,
            title="فاکتور جدید برای تأیید",
            body="یک نسخهٔ امضاشده از فروشنده دریافت کرده‌اید.",
            link=f"/app/invoices/{invoice_id}/",
        )
    )
    return invoice


@transaction.atomic
def cancel_pending_partner_invoice(
    *, invoice: SalesInvoice, membership: BusinessMembership, reason: str
) -> SalesInvoice:
    invoice = SalesInvoice.objects.select_for_update().get(pk=invoice.pk)
    _require(membership, INVOICE_SEND, "اجازه لغو فاکتور ارسالی را ندارید.")
    if membership.business_id != invoice.seller_business_id:
        raise InvoiceError("فقط فرستنده می‌تواند فاکتور در انتظار را لغو کند.")
    if invoice.status == SalesInvoice.Status.CANCELLED_BY_SENDER:
        return invoice
    if invoice.status != SalesInvoice.Status.AWAITING_CONFIRMATION:
        raise InvoiceError("فقط فاکتور در انتظار تأیید قابل لغو است.")
    reason = str(reason or "").strip()
    if not reason:
        raise InvoiceError("علت لغو را وارد کنید.")
    revision = InvoiceRevision.objects.select_for_update().get(
        invoice=invoice, revision_number=invoice.current_revision_number
    )
    revision.state = InvoiceRevision.State.CANCELLED
    revision.decided_by = membership.user
    revision.decided_business = membership.business
    revision.decided_at = timezone.now()
    revision.rejection_reason = reason
    revision.save(update_fields=["state", "decided_by", "decided_business", "decided_at", "rejection_reason"])
    invoice.status = SalesInvoice.Status.CANCELLED_BY_SENDER
    invoice.cancel_reason = reason
    invoice.cancelled_at = timezone.now()
    invoice.cancelled_by = membership.user
    invoice.save(update_fields=["status", "cancel_reason", "cancelled_at", "cancelled_by", "updated_at"])
    transaction.on_commit(
        lambda buyer=invoice.buyer_business, invoice_id=invoice.id: notify_business(
            buyer,
            capability=INVOICE_CONFIRM,
            title="فاکتور در انتظار لغو شد",
            body="فروشنده فاکتور ارسالی را پیش از تأیید لغو کرد.",
            link=f"/app/invoices/{invoice_id}/",
        )
    )
    return invoice


@transaction.atomic
def reject_partner_invoice(*, invoice: SalesInvoice, membership: BusinessMembership, reason: str) -> SalesInvoice:
    invoice = SalesInvoice.objects.select_for_update().get(pk=invoice.pk)
    _require(membership, INVOICE_CONFIRM, "اجازه رد یا تأیید فاکتور دریافتی را ندارید.")
    if membership.business_id != invoice.buyer_business_id:
        raise InvoiceError("فقط کسب‌وکار خریدار می‌تواند فاکتور را رد کند.")
    reason = str(reason or "").strip()
    if not reason:
        raise InvoiceError("علت رد فاکتور الزامی است.")
    if invoice.status != SalesInvoice.Status.AWAITING_CONFIRMATION:
        raise InvoiceError("فاکتور در انتظار تأیید نیست.")
    revision = InvoiceRevision.objects.select_for_update().get(
        invoice=invoice, revision_number=invoice.current_revision_number, state=InvoiceRevision.State.SENT
    )
    revision.state = InvoiceRevision.State.REJECTED
    revision.rejection_reason = reason
    revision.decided_by = membership.user
    revision.decided_business = membership.business
    revision.decided_at = timezone.now()
    revision.save(update_fields=["state", "rejection_reason", "decided_by", "decided_business", "decided_at"])
    invoice.status = SalesInvoice.Status.DRAFT
    invoice.version += 1
    invoice.save(update_fields=["status", "version", "updated_at"])
    transaction.on_commit(
        lambda seller=invoice.seller_business, invoice_id=invoice.id: notify_business(
            seller,
            capability=INVOICE_SEND,
            title="فاکتور برای اصلاح برگشت خورد",
            body="خریدار نسخهٔ ارسالی را رد کرده است؛ علت در تاریخچهٔ نسخه ثبت شده است.",
            link=f"/app/invoices/{invoice_id}/",
        )
    )
    return invoice


@transaction.atomic
def confirm_partner_invoice(*, invoice: SalesInvoice, membership: BusinessMembership) -> SalesInvoice:
    invoice = (
        SalesInvoice.objects.select_for_update(of=("self",))
        .select_related("seller_business", "buyer_business")
        .prefetch_related("items")
        .get(pk=invoice.pk)
    )
    _require(membership, INVOICE_CONFIRM, "اجازه تأیید فاکتور دریافتی را ندارید.")
    if membership.business_id != invoice.buyer_business_id:
        raise InvoiceError("فقط خریدار ثبت‌شده می‌تواند این فاکتور را تأیید کند.")
    if invoice.status == SalesInvoice.Status.CONFIRMED:
        return invoice
    if invoice.status != SalesInvoice.Status.AWAITING_CONFIRMATION:
        raise InvoiceError("فاکتور در انتظار تأیید نیست.")
    _validate_settlement(invoice)
    revision = InvoiceRevision.objects.select_for_update().get(
        invoice=invoice, revision_number=invoice.current_revision_number, state=InvoiceRevision.State.SENT
    )
    buyer_settings = BusinessInvoiceSettings.objects.filter(business=invoice.buyer_business).first()
    personal = UserInvoiceSignature.objects.filter(user=membership.user).first()
    if buyer_settings is None or not buyer_settings.signature:
        raise InvoiceError("امضای رسمی کسب‌وکار خریدار ثبت نشده است؛ ابتدا تنظیمات فاکتور را کامل کنید.")
    if personal is None or not personal.image:
        raise InvoiceError("امضای شخصی شما ثبت نشده است؛ ابتدا تنظیمات امضای شخصی را کامل کنید.")
    _copy_asset(revision, "buyer_business_signature", buyer_settings.signature, "buyer-business.png")
    _copy_asset(revision, "buyer_user_signature", personal.image, "buyer-user.png")
    revision.state = InvoiceRevision.State.CONFIRMED
    revision.decided_by = membership.user
    revision.decided_business = membership.business
    revision.decided_at = timezone.now()
    revision.save()
    Business.objects.select_for_update().get(pk=invoice.seller_business_id)
    invoice.number = invoice.number or allocate_number(invoice.seller_business)
    invoice.status = SalesInvoice.Status.CONFIRMED
    invoice.confirmed_at = revision.decided_at
    invoice.confirmed_by = membership.user
    invoice.issued_at = revision.decided_at
    invoice.issued_by = invoice.sent_by
    invoice.paid_amount = invoice.cash_amount + invoice.cheque_amount
    invoice.payment_status = (
        SalesInvoice.PaymentStatus.PAID if not invoice.credit_amount else SalesInvoice.PaymentStatus.PARTIAL
    )
    invoice.save()
    _create_trade(invoice, membership.user)
    from apps.accounting.services import post_invoice_entries

    post_invoice_entries(invoice=invoice, membership=membership)
    _create_settlement_events(invoice, revision, membership.user)
    for business in (invoice.seller_business, invoice.buyer_business):
        transaction.on_commit(
            lambda target=business, invoice_id=invoice.id: notify_business(
                target,
                capability="invoice.view",
                title="فاکتور همکار نهایی شد",
                body="تأیید دوطرفه ثبت شد و سند نهایی در دسترس است.",
                link=f"/app/invoices/{invoice_id}/",
            )
        )
    return invoice


@transaction.atomic
def confirm_local_invoice_offline(
    *,
    invoice: SalesInvoice,
    membership: BusinessMembership,
    signer_name: str,
    confirmed_at,
    signature,
    attested: bool,
) -> SalesInvoice:
    invoice = (
        SalesInvoice.objects.select_for_update(of=("self",))
        .select_related("seller_business", "local_counterparty")
        .prefetch_related("items")
        .get(pk=invoice.pk)
    )
    _require(membership, INVOICE_OFFLINE_APPROVE, "اجازه ثبت تأیید آفلاین را ندارید.")
    if membership.business_id != invoice.seller_business_id:
        raise InvoiceError("دسترسی نامعتبر است.")
    if invoice.counterparty_type != SalesInvoice.Counterparty.LOCAL:
        raise InvoiceError("تأیید آفلاین فقط برای همکار محلی است.")
    if invoice.status == SalesInvoice.Status.CONFIRMED:
        return invoice
    if invoice.status != SalesInvoice.Status.DRAFT:
        raise InvoiceError("این فاکتور قابل تأیید آفلاین نیست.")
    if not attested:
        raise InvoiceError("ثبت دریافت تأیید خارج از سنگا باید صریحاً گواهی شود.")
    signer_name = str(signer_name or "").strip()
    if not signer_name or not confirmed_at or not signature:
        raise InvoiceError("نام امضاکننده، زمان تأیید و تصویر امضای همان فاکتور الزامی است.")
    _validate_settlement(invoice)
    revision = _freeze_revision(invoice, membership)
    _copy_asset(revision, "offline_buyer_signature", signature, "offline-buyer.png")
    revision.state = InvoiceRevision.State.CONFIRMED
    revision.decided_by = membership.user
    revision.decided_business = invoice.seller_business
    revision.decided_at = timezone.now()
    revision.offline_signer_name = signer_name
    revision.offline_confirmed_at = confirmed_at
    revision.offline_recorded_by = membership.user
    revision.save()
    Business.objects.select_for_update().get(pk=invoice.seller_business_id)
    invoice.number = invoice.number or allocate_number(invoice.seller_business)
    invoice.status = SalesInvoice.Status.CONFIRMED
    invoice.offline_confirmation = True
    invoice.confirmed_at = confirmed_at
    invoice.confirmed_by = membership.user
    invoice.issued_at = timezone.now()
    invoice.issued_by = membership.user
    invoice.seller_snapshot = seller_snapshot(invoice.seller_business, get_invoice_settings(invoice.seller_business))
    invoice.paid_amount = invoice.cash_amount + invoice.cheque_amount
    invoice.payment_status = (
        SalesInvoice.PaymentStatus.PAID if not invoice.credit_amount else SalesInvoice.PaymentStatus.PARTIAL
    )
    invoice.save()
    _create_trade(invoice, membership.user)
    from apps.accounting.services import post_invoice_entries

    post_invoice_entries(invoice=invoice, membership=membership)
    _create_settlement_events(invoice, revision, membership.user)
    return invoice


@transaction.atomic
def change_cheque_status(
    *, cheque: ChequeReceivable, membership: BusinessMembership, status: str, reason: str = ""
) -> ChequeReceivable:
    cheque = (
        ChequeReceivable.objects.select_for_update(of=("self",))
        .select_related("invoice__seller_business", "invoice__buyer_business", "settlement_event__revision")
        .get(pk=cheque.pk)
    )
    _require(membership, CHEQUE_MANAGE, "اجازه مدیریت وضعیت چک را ندارید.")
    if membership.business_id != cheque.invoice.seller_business_id:
        raise InvoiceError("فقط فروشنده می‌تواند وضعیت چک دریافتی را تغییر دهد.")
    allowed = {
        ChequeReceivable.Status.RECEIVED: {
            ChequeReceivable.Status.IN_COLLECTION,
            ChequeReceivable.Status.CLEARED,
            ChequeReceivable.Status.BOUNCED,
            ChequeReceivable.Status.RETURNED,
        },
        ChequeReceivable.Status.IN_COLLECTION: {
            ChequeReceivable.Status.CLEARED,
            ChequeReceivable.Status.BOUNCED,
            ChequeReceivable.Status.RETURNED,
        },
    }
    if cheque.status == status:
        return cheque
    if status not in allowed.get(cheque.status, set()):
        raise InvoiceError("تغییر وضعیت چک مجاز نیست.")
    event_key = f"cheque:{cheque.id}:{status}"
    if ChequeEvent.objects.filter(idempotency_key=event_key).exists():
        return cheque
    previous = cheque.status
    cheque.status = status
    cheque.save(update_fields=["status"])
    ChequeEvent.objects.create(
        cheque=cheque,
        from_status=previous,
        to_status=status,
        reason=str(reason or "").strip(),
        idempotency_key=event_key,
        recorded_by=membership.user,
        occurred_at=timezone.now(),
    )
    if status in {ChequeReceivable.Status.BOUNCED, ChequeReceivable.Status.RETURNED}:
        from apps.accounting.services import reverse_invoice_settlement_entries

        reverse_invoice_settlement_entries(invoice=cheque.invoice, kind="cheque", actor=membership.user)
        original = cheque.settlement_event
        SettlementEvent.objects.get_or_create(
            idempotency_key=f"{original.idempotency_key}:reversal",
            defaults={
                "invoice": original.invoice,
                "revision": original.revision,
                "kind": original.kind,
                "event_type": SettlementEvent.EventType.REVERSAL,
                "amount": original.amount,
                "currency": original.currency,
                "reverses": original,
                "recorded_by": membership.user,
                "occurred_at": timezone.now(),
            },
        )
    return cheque


@transaction.atomic
def propose_counterparty_link(
    *, local_counterparty: LocalCounterparty, target: Business, membership: BusinessMembership
) -> CounterpartyLinkProposal:
    _require(membership, COUNTERPARTY_LINK_PROPOSE, "اجازه پیشنهاد اتصال همکار را ندارید.")
    if membership.business_id != local_counterparty.owner_business_id:
        raise InvoiceError("دسترسی نامعتبر است.")
    if local_counterparty.linked_business_id:
        raise InvoiceError("این همکار محلی قبلاً به یک کسب‌وکار متصل شده است.")
    if target.id == local_counterparty.owner_business_id or not business_is_network_eligible(target):
        raise InvoiceError("کسب‌وکار مقصد برای اتصال واجد شرایط نیست.")
    proposal, _ = CounterpartyLinkProposal.objects.get_or_create(
        local_counterparty=local_counterparty,
        target_business=target,
        status=CounterpartyLinkProposal.Status.PENDING,
        defaults={"proposed_by": membership.user},
    )
    return proposal


@transaction.atomic
def cancel_counterparty_link(
    *, proposal: CounterpartyLinkProposal, membership: BusinessMembership
) -> CounterpartyLinkProposal:
    proposal = CounterpartyLinkProposal.objects.select_for_update(of=("self",)).select_related("local_counterparty").get(
        pk=proposal.pk
    )
    _require(membership, COUNTERPARTY_LINK_PROPOSE, "اجازه لغو پیشنهاد اتصال را ندارید.")
    if membership.business_id != proposal.local_counterparty.owner_business_id:
        raise InvoiceError("فقط پیشنهاددهنده می‌تواند اتصال در انتظار را لغو کند.")
    if proposal.status != CounterpartyLinkProposal.Status.PENDING:
        return proposal
    proposal.status = CounterpartyLinkProposal.Status.CANCELLED
    proposal.decided_by = membership.user
    proposal.decided_at = timezone.now()
    proposal.save(update_fields=["status", "decided_by", "decided_at"])
    return proposal


@transaction.atomic
def decide_counterparty_link(
    *, proposal: CounterpartyLinkProposal, membership: BusinessMembership, approve: bool, reason: str = ""
) -> CounterpartyLinkProposal:
    proposal = (
        CounterpartyLinkProposal.objects.select_for_update(of=("self",))
        .select_related("local_counterparty__owner_business", "target_business")
        .get(pk=proposal.pk)
    )
    _require(membership, COUNTERPARTY_LINK_APPROVE, "اجازه تصمیم‌گیری درباره اتصال همکار را ندارید.")
    if membership.business_id != proposal.target_business_id:
        raise InvoiceError("فقط کسب‌وکار مقصد می‌تواند انتقال سابقه را تأیید کند.")
    if proposal.status != CounterpartyLinkProposal.Status.PENDING:
        return proposal
    proposal.decided_by = membership.user
    proposal.decided_at = timezone.now()
    proposal.decision_reason = str(reason or "").strip()
    if not approve:
        proposal.status = CounterpartyLinkProposal.Status.REJECTED
        proposal.save()
        return proposal
    proposal.status = CounterpartyLinkProposal.Status.APPROVED
    proposal.import_batch_id = proposal.import_batch_id or uuid.uuid4()
    proposal.save()
    local = proposal.local_counterparty
    local.linked_business = proposal.target_business
    local.save(update_fields=["linked_business", "updated_at"])
    from apps.accounting.models import LedgerEntry
    from apps.accounting.services import _lock_both, _write_entry

    locked = _lock_both(local.owner_business, proposal.target_business)
    seller = locked[str(local.owner_business_id)]
    buyer = locked[str(proposal.target_business_id)]
    invoices = SalesInvoice.objects.filter(
        local_counterparty=local,
        status=SalesInvoice.Status.CONFIRMED,
        offline_confirmation=True,
    ).select_related("trade")
    for invoice in invoices:
        _write_entry(
            business=buyer,
            counterparty=seller,
            entry_type=LedgerEntry.Type.PURCHASE,
            amount=invoice.total_amount,
            description=f"انتقال سابقه فاکتور {invoice.number}",
            reference=f"import:{proposal.import_batch_id}",
            idempotency_key=f"link:{proposal.id}:invoice:{invoice.id}:purchase",
            actor=membership.user,
        )
        for kind, amount in (("cash", invoice.cash_amount), ("cheque", invoice.cheque_amount)):
            if amount:
                _write_entry(
                    business=buyer,
                    counterparty=seller,
                    entry_type=LedgerEntry.Type.PAYMENT_MADE,
                    amount=amount,
                    description=f"انتقال تسویه {kind} فاکتور {invoice.number}",
                    reference=f"import:{proposal.import_batch_id}",
                    idempotency_key=f"link:{proposal.id}:invoice:{invoice.id}:{kind}",
                    actor=membership.user,
                )
    return proposal
