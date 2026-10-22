import threading
from decimal import Decimal

import pytest
from django.db import connection, transaction
from django.urls import reverse

from apps.accounting.models import LedgerEntry
from apps.core.testing import make_business, owner_membership
from apps.invoicing.models import InvoiceRevision, SalesInvoice, SettlementEvent
from apps.invoicing.partner_services import (
    cancel_pending_partner_invoice,
    confirm_partner_invoice,
    create_partner_draft,
    reject_partner_invoice,
    send_partner_invoice,
    update_partner_draft,
)
from apps.invoicing.services import InvoiceError
from apps.invoicing.tests.test_invoice_first_lifecycle import install_signatures, lines


@pytest.fixture
def sent_invoice(db, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    seller = make_business(name="فروشنده نسخه", owner_phone="09129995001")
    buyer = make_business(name="خریدار نسخه", owner_phone="09129995002")
    seller_member, buyer_member = owner_membership(seller), owner_membership(buyer)
    install_signatures(seller, seller_member)
    install_signatures(buyer, buyer_member)
    invoice = create_partner_draft(
        business=seller, membership=seller_member, buyer_business=buyer, lines=lines(),
        settlement_method=SalesInvoice.SettlementMethod.CREDIT, credit_amount=Decimal("100"),
    )
    invoice = send_partner_invoice(invoice=invoice, membership=seller_member)
    return invoice, seller_member, buyer_member


def resend(client, invoice, seller, buyer):
    client.force_login(buyer.user)
    client.post(reverse("invoicing:reject", args=[invoice.pk]), {"revision_number": 1, "reason": "اصلاح قیمت"})
    invoice.refresh_from_db()
    assert invoice.status == SalesInvoice.Status.DRAFT
    update_partner_draft(
        invoice=invoice, membership=seller, lines=lines("120"),
        settlement_method=SalesInvoice.SettlementMethod.CREDIT, credit_amount=Decimal("120"),
    )
    return send_partner_invoice(invoice=invoice, membership=seller)


@pytest.mark.parametrize("action", ["confirm", "reject", "cancel_pending"])
def test_old_page_cannot_decide_a_resent_revision(client, sent_invoice, action):
    invoice, seller, buyer = sent_invoice
    invoice = resend(client, invoice, seller, buyer)
    client.force_login(seller.user if action == "cancel_pending" else buyer.user)
    response = client.post(reverse(f"invoicing:{action}", args=[invoice.pk]), {
        "revision_number": "1", "reason": "تصمیم صفحه قدیمی",
    }, follow=True)
    invoice.refresh_from_db()
    assert invoice.status == SalesInvoice.Status.AWAITING_CONFIRMATION
    assert invoice.current_revision_number == 2
    assert invoice.revisions.get(revision_number=2).state == InvoiceRevision.State.SENT
    assert not LedgerEntry.objects.filter(related_invoice=invoice).exists()
    assert not SettlementEvent.objects.filter(invoice=invoice).exists()
    assert "نسخه" in response.content.decode()


@pytest.mark.concurrency
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("competing_action", ["confirm", "cancel"])
def test_simultaneous_decisions_have_one_financial_outcome(sent_invoice, competing_action):
    invoice, seller, buyer = sent_invoice
    barrier = threading.Barrier(2, timeout=15)
    outcomes, errors = [], []

    def decide(index):
        try:
            barrier.wait()
            if index == 0 or competing_action == "confirm":
                result = confirm_partner_invoice(invoice=invoice, membership=buyer, expected_revision=1)
            else:
                result = cancel_pending_partner_invoice(
                    invoice=invoice, membership=seller, expected_revision=1, reason="لغو هم‌زمان",
                )
            outcomes.append(result.status)
        except Exception as exc:
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=decide, args=(index,), daemon=True) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads), "invoice decisions deadlocked"
    invoice.refresh_from_db()
    if competing_action == "confirm":
        assert errors == []
        assert outcomes == [SalesInvoice.Status.CONFIRMED] * 2
    else:
        assert len(outcomes) == len(errors) == 1
        assert isinstance(errors[0], InvoiceError)
    confirmed = invoice.status == SalesInvoice.Status.CONFIRMED
    assert invoice.status in {SalesInvoice.Status.CONFIRMED, SalesInvoice.Status.CANCELLED_BY_SENDER}
    assert LedgerEntry.objects.filter(related_invoice=invoice).count() == (2 if confirmed else 0)
    assert SettlementEvent.objects.filter(invoice=invoice).count() == (1 if confirmed else 0)
    assert bool(invoice.trade_id) == confirmed


@pytest.mark.concurrency
@pytest.mark.django_db(transaction=True)
def test_revision_is_checked_after_waiting_for_the_invoice_lock(sent_invoice):
    invoice, seller, buyer = sent_invoice
    locking_query_started = threading.Event()
    errors = []

    def observe_lock(execute, sql, params, many, context):
        if "FOR UPDATE" in sql:
            locking_query_started.set()
        return execute(sql, params, many, context)

    def old_page_confirmation():
        try:
            with connection.execute_wrapper(observe_lock):
                confirm_partner_invoice(invoice=invoice, membership=buyer, expected_revision=1)
        except Exception as exc:
            errors.append(exc)
        finally:
            connection.close()

    with transaction.atomic():
        SalesInvoice.objects.select_for_update().get(pk=invoice.pk)
        thread = threading.Thread(target=old_page_confirmation, daemon=True)
        thread.start()
        assert locking_query_started.wait(timeout=15)
        reject_partner_invoice(invoice=invoice, membership=buyer, expected_revision=1, reason="اصلاح قیمت")
        update_partner_draft(
            invoice=invoice, membership=seller, lines=lines("120"),
            settlement_method=SalesInvoice.SettlementMethod.CREDIT, credit_amount=Decimal("120"),
        )
        send_partner_invoice(invoice=invoice, membership=seller)
    thread.join(timeout=30)
    assert not thread.is_alive(), "old confirmation did not release its connection"
    assert len(errors) == 1 and isinstance(errors[0], InvoiceError)
    assert "نسخه" in str(errors[0])
    invoice.refresh_from_db()
    assert invoice.current_revision_number == 2
    assert invoice.status == SalesInvoice.Status.AWAITING_CONFIRMATION
    assert not LedgerEntry.objects.filter(related_invoice=invoice).exists()
    assert not SettlementEvent.objects.filter(invoice=invoice).exists()


@pytest.mark.parametrize("action", ["confirm", "reject", "cancel_pending"])
@pytest.mark.parametrize("revision", [None, "invalid", "0", "-1"])
def test_decisions_require_a_valid_revision(client, sent_invoice, action, revision):
    invoice, seller, buyer = sent_invoice
    client.force_login(seller.user if action == "cancel_pending" else buyer.user)
    data = {"reason": "بررسی"}
    if revision is not None:
        data["revision_number"] = revision
    assert client.post(reverse(f"invoicing:{action}", args=[invoice.pk]), data).status_code == 302
    invoice.refresh_from_db()
    assert invoice.status == SalesInvoice.Status.AWAITING_CONFIRMATION
    assert not LedgerEntry.objects.filter(related_invoice=invoice).exists()


def test_current_revision_is_rendered_and_confirmation_retry_posts_once(client, sent_invoice):
    invoice, seller, buyer = sent_invoice
    invoice = resend(client, invoice, seller, buyer)
    page = client.get(reverse("invoicing:detail", args=[invoice.pk]))
    assert 'name="revision_number" value="2"' in page.content.decode()
    for _ in range(2):
        response = client.post(reverse("invoicing:confirm", args=[invoice.pk]), {"revision_number": "2"})
        assert response.status_code == 302
    invoice.refresh_from_db()
    assert invoice.status == SalesInvoice.Status.CONFIRMED
    assert LedgerEntry.objects.filter(related_invoice=invoice).count() == 2
    assert SettlementEvent.objects.filter(invoice=invoice).count() == 1


def test_sender_can_cancel_pending_revision_without_finalizing_or_posting(client, sent_invoice):
    invoice, seller, _ = sent_invoice
    client.force_login(seller.user)
    for _ in range(2):
        response = client.post(reverse("invoicing:cancel_pending", args=[invoice.pk]), {
            "revision_number": "1", "reason": "لغو پیش از تأیید",
        })
        assert response.status_code == 302
    invoice.refresh_from_db()
    assert invoice.status == SalesInvoice.Status.CANCELLED_BY_SENDER
    assert not invoice.number and invoice.issued_at is None and invoice.trade_id is None
    assert invoice.cancel_reason == "لغو پیش از تأیید" and invoice.cancelled_by == seller.user
    assert invoice.revisions.get(revision_number=1).state == InvoiceRevision.State.CANCELLED
    assert not LedgerEntry.objects.filter(related_invoice=invoice).exists()
    assert not SettlementEvent.objects.filter(invoice=invoice).exists()

