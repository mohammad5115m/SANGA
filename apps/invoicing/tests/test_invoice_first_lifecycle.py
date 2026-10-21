from __future__ import annotations

import base64
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.accounting.models import LedgerEntry
from apps.core.testing import make_business, owner_membership
from apps.invoicing.models import (
    BusinessInvoiceSettings,
    ChequeEvent,
    ChequeReceivable,
    CounterpartyLinkProposal,
    InvoiceRevision,
    SalesInvoice,
    SettlementEvent,
    UserInvoiceSignature,
)
from apps.invoicing.partner_services import (
    change_cheque_status,
    confirm_local_invoice_offline,
    confirm_partner_invoice,
    create_partner_draft,
    decide_counterparty_link,
    propose_counterparty_link,
    reject_partner_invoice,
    resolve_local_counterparty,
    send_partner_invoice,
    update_partner_draft,
)
from apps.invoicing.services import InvoiceError, create_manual_invoice, issue_invoice
from apps.reporting.reports import DateRange, invoice_summary, sales_summary

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")


def signature(name: str):
    return SimpleUploadedFile(name, PNG, content_type="image/png")


def lines(price="100"):
    return [
        {
            "product_name": "سنگ تست",
            "quantity": Decimal("1"),
            "unit": "متر مربع",
            "unit_price": Decimal(price),
            "item": None,
        }
    ]


def install_signatures(business, membership):
    BusinessInvoiceSettings.objects.create(business=business, signature=signature(f"business-{business.id}.png"))
    UserInvoiceSignature.objects.create(user=membership.user, image=signature(f"user-{membership.user_id}.png"))


@pytest.mark.django_db
def test_customer_invoice_requires_full_receipt_and_creates_no_ledger_entry():
    seller = make_business(name="فروشنده مشتری", owner_phone="09001000001")
    membership = owner_membership(seller)
    draft = create_manual_invoice(
        business=seller,
        membership=membership,
        lines=lines(),
        customer_name="مشتری نهایی",
        paid_amount=Decimal("0"),
        issue=False,
    )
    with pytest.raises(InvoiceError, match="دریافت کامل"):
        issue_invoice(invoice=draft, membership=membership)
    draft.paid_amount = draft.total_amount
    draft.amount_due = Decimal("0")
    draft.payment_status = SalesInvoice.PaymentStatus.PAID
    draft.save(update_fields=["paid_amount", "amount_due", "payment_status", "updated_at"])
    issued = issue_invoice(invoice=draft, membership=membership)
    assert issued.status == SalesInvoice.Status.ISSUED
    assert issued.trade_id is not None
    assert LedgerEntry.objects.filter(related_invoice=issued).count() == 0


@pytest.mark.django_db
def test_registered_partner_confirm_freezes_both_signatures_and_posts_once():
    seller = make_business(name="فروشنده همکار", owner_phone="09001000002")
    buyer = make_business(name="خریدار همکار", owner_phone="09001000003")
    seller_member = owner_membership(seller)
    buyer_member = owner_membership(buyer)
    install_signatures(seller, seller_member)
    install_signatures(buyer, buyer_member)
    invoice = create_partner_draft(
        business=seller,
        membership=seller_member,
        buyer_business=buyer,
        lines=lines(),
        settlement_method=SalesInvoice.SettlementMethod.CREDIT,
        credit_amount=Decimal("100"),
    )
    sent = send_partner_invoice(invoice=invoice, membership=seller_member)
    assert sent.status == SalesInvoice.Status.AWAITING_CONFIRMATION
    confirmed = confirm_partner_invoice(invoice=sent, membership=buyer_member)
    revision = InvoiceRevision.objects.get(invoice=confirmed, revision_number=1)
    assert confirmed.status == SalesInvoice.Status.CONFIRMED
    assert revision.seller_business_signature and revision.seller_user_signature
    assert revision.buyer_business_signature and revision.buyer_user_signature
    assert LedgerEntry.objects.filter(related_invoice=confirmed).count() == 2
    confirm_partner_invoice(invoice=confirmed, membership=buyer_member)
    assert LedgerEntry.objects.filter(related_invoice=confirmed).count() == 2


@pytest.mark.django_db
def test_local_offline_confirmation_records_local_ledger_only():
    seller = make_business(name="فروشنده محلی", owner_phone="09001000004")
    membership = owner_membership(seller)
    install_signatures(seller, membership)
    local = resolve_local_counterparty(
        business=seller,
        membership=membership,
        name="همکار خارج از سامانه",
        phone="09120000000",
    )
    invoice = create_partner_draft(
        business=seller,
        membership=membership,
        local_counterparty=local,
        lines=lines(),
        settlement_method=SalesInvoice.SettlementMethod.CASH,
        cash_amount=Decimal("100"),
    )
    confirmed = confirm_local_invoice_offline(
        invoice=invoice,
        membership=membership,
        signer_name="نماینده خریدار",
        confirmed_at=timezone.now(),
        signature=signature("offline.png"),
        attested=True,
    )
    assert confirmed.offline_confirmation is True
    assert LedgerEntry.objects.filter(related_invoice=confirmed, local_counterparty=local).count() == 2
    assert not LedgerEntry.objects.filter(related_invoice=confirmed, counterparty_business__isnull=False).exists()


@pytest.mark.django_db
def test_rejection_preserves_revision_and_resends_same_invoice_identity():
    seller = make_business(name="فروشنده اصلاح", owner_phone="09001000005")
    buyer = make_business(name="خریدار اصلاح", owner_phone="09001000006")
    seller_member = owner_membership(seller)
    buyer_member = owner_membership(buyer)
    install_signatures(seller, seller_member)
    install_signatures(buyer, buyer_member)
    invoice = create_partner_draft(
        business=seller,
        membership=seller_member,
        buyer_business=buyer,
        lines=lines(),
        settlement_method=SalesInvoice.SettlementMethod.CREDIT,
        credit_amount=Decimal("100"),
    )
    send_partner_invoice(invoice=invoice, membership=seller_member)
    rejected_id = invoice.id
    first_hash = invoice.revisions.get(revision_number=1).payload_hash
    reject_partner_invoice(invoice=invoice, membership=buyer_member, reason="قیمت نیاز به اصلاح دارد")
    invoice.refresh_from_db()
    first = invoice.revisions.get(revision_number=1)
    assert invoice.id == rejected_id
    assert invoice.status == SalesInvoice.Status.DRAFT
    assert first.state == InvoiceRevision.State.REJECTED
    assert first.rejection_reason == "قیمت نیاز به اصلاح دارد"
    assert first.payload_hash == first_hash
    assert invoice.trade_id is None
    assert not LedgerEntry.objects.filter(related_invoice=invoice).exists()

    update_partner_draft(
        invoice=invoice,
        membership=seller_member,
        lines=lines("120"),
        settlement_method=SalesInvoice.SettlementMethod.CREDIT,
        credit_amount=Decimal("120"),
    )
    resent = send_partner_invoice(invoice=invoice, membership=seller_member)
    assert resent.id == rejected_id
    assert resent.current_revision_number == 2
    assert resent.revisions.get(revision_number=1).payload_hash == first_hash
    assert resent.revisions.get(revision_number=2).payload_hash != first_hash


@pytest.mark.django_db
def test_cheque_bounce_restores_registered_balances_exactly_once():
    seller = make_business(name="فروشنده چک", owner_phone="09001000007")
    buyer = make_business(name="خریدار چک", owner_phone="09001000008")
    seller_member = owner_membership(seller)
    buyer_member = owner_membership(buyer)
    install_signatures(seller, seller_member)
    install_signatures(buyer, buyer_member)
    invoice = create_partner_draft(
        business=seller,
        membership=seller_member,
        buyer_business=buyer,
        lines=lines(),
        settlement_method=SalesInvoice.SettlementMethod.CHEQUE,
        cheque_amount=Decimal("100"),
        cheque_details={
            "reference_number": "CHK-100",
            "bank": "بانک تست",
            "due_date": timezone.localdate().isoformat(),
            "drawer_name": "خریدار",
        },
    )
    send_partner_invoice(invoice=invoice, membership=seller_member)
    confirmed = confirm_partner_invoice(invoice=invoice, membership=buyer_member)
    cheque = confirmed.cheques.get()
    assert LedgerEntry.objects.filter(related_invoice=confirmed).count() == 4
    assert ChequeEvent.objects.filter(cheque=cheque).count() == 1

    change_cheque_status(
        cheque=cheque,
        membership=seller_member,
        status=ChequeReceivable.Status.BOUNCED,
        reason="برگشت آزمون",
    )
    assert LedgerEntry.objects.filter(related_invoice=confirmed).count() == 6
    assert SettlementEvent.objects.filter(
        invoice=confirmed, event_type=SettlementEvent.EventType.REVERSAL
    ).count() == 1
    change_cheque_status(
        cheque=cheque,
        membership=seller_member,
        status=ChequeReceivable.Status.BOUNCED,
        reason="تکرار",
    )
    assert LedgerEntry.objects.filter(related_invoice=confirmed).count() == 6


@pytest.mark.django_db
def test_local_history_link_requires_target_approval_and_imports_once():
    seller = make_business(name="مالک سابقه محلی", owner_phone="09001000009")
    target = make_business(name="همکار ثبت‌شده مقصد", owner_phone="09001000010")
    seller_member = owner_membership(seller)
    target_member = owner_membership(target)
    install_signatures(seller, seller_member)
    local = resolve_local_counterparty(
        business=seller,
        membership=seller_member,
        name="همکار قابل اتصال",
        phone="09121111111",
    )
    invoice = create_partner_draft(
        business=seller,
        membership=seller_member,
        local_counterparty=local,
        lines=lines(),
        settlement_method=SalesInvoice.SettlementMethod.CASH,
        cash_amount=Decimal("100"),
    )
    confirm_local_invoice_offline(
        invoice=invoice,
        membership=seller_member,
        signer_name="نماینده محلی",
        confirmed_at=timezone.now(),
        signature=signature("link-offline.png"),
        attested=True,
    )
    proposal = propose_counterparty_link(
        local_counterparty=local,
        target=target,
        membership=seller_member,
    )
    local.refresh_from_db()
    assert local.linked_business_id is None
    assert not LedgerEntry.objects.filter(business=target).exists()

    decided = decide_counterparty_link(
        proposal=proposal,
        membership=target_member,
        approve=True,
    )
    local.refresh_from_db()
    assert decided.status == CounterpartyLinkProposal.Status.APPROVED
    assert local.linked_business_id == target.id
    assert LedgerEntry.objects.filter(business=target, reference__startswith="import:").count() == 2
    decide_counterparty_link(proposal=decided, membership=target_member, approve=True)
    assert LedgerEntry.objects.filter(business=target, reference__startswith="import:").count() == 2
    assert LedgerEntry.objects.filter(business=seller, local_counterparty=local).count() == 2


@pytest.mark.django_db
def test_customer_registered_and_local_sales_share_reporting_source():
    seller = make_business(name="فروشنده گزارش", owner_phone="09001000011")
    buyer = make_business(name="خریدار گزارش", owner_phone="09001000012")
    seller_member = owner_membership(seller)
    buyer_member = owner_membership(buyer)
    install_signatures(seller, seller_member)
    install_signatures(buyer, buyer_member)

    create_manual_invoice(
        business=seller,
        membership=seller_member,
        lines=lines("100"),
        customer_name="مشتری گزارش",
        paid_amount=Decimal("100"),
        issue=True,
    )
    partner = create_partner_draft(
        business=seller,
        membership=seller_member,
        buyer_business=buyer,
        lines=lines("200"),
        settlement_method=SalesInvoice.SettlementMethod.CREDIT,
        credit_amount=Decimal("200"),
    )
    send_partner_invoice(invoice=partner, membership=seller_member)
    confirm_partner_invoice(invoice=partner, membership=buyer_member)
    local = resolve_local_counterparty(
        business=seller,
        membership=seller_member,
        name="همکار محلی گزارش",
    )
    local_invoice = create_partner_draft(
        business=seller,
        membership=seller_member,
        local_counterparty=local,
        lines=lines("300"),
        settlement_method=SalesInvoice.SettlementMethod.CREDIT,
        credit_amount=Decimal("300"),
    )
    confirm_local_invoice_offline(
        invoice=local_invoice,
        membership=seller_member,
        signer_name="نماینده محلی",
        confirmed_at=timezone.now(),
        signature=signature("report-offline.png"),
        attested=True,
    )

    sales = sales_summary(seller, DateRange())
    invoices = invoice_summary(seller, DateRange())
    assert sales["trade_count"] == 3
    assert sales["total"] == Decimal("600.00")
    assert invoices["issued_count"] == 3
    assert invoices["total"] == Decimal("600.00")
