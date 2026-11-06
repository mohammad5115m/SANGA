"""Business restrictions must survive stale pages and apply before financial writes."""

import threading
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection, transaction
from django.urls import reverse
from django.utils import timezone

from apps.accounting.models import LedgerEntry
from apps.accounting.services import LedgerError, post_invoice_entries
from apps.businesses.models import Business
from apps.core.testing import make_business, make_item, owner_membership
from apps.invoicing.models import (
    ChequeEvent,
    ChequeReceivable,
    CounterpartyLinkProposal,
    InvoiceRevision,
    LocalCounterparty,
    SalesInvoice,
    SettlementEvent,
)
from apps.invoicing.partner_services import (
    cancel_counterparty_link,
    cancel_pending_partner_invoice,
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
from apps.invoicing.services import InvoiceError
from apps.invoicing.tests.test_invoice_first_lifecycle import install_signatures, lines, signature
from apps.trading.models import Trade


@pytest.fixture
def pair(db, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    seller = make_business(name="فروشنده مجاز", owner_phone="09129996001")
    buyer = make_business(name="خریدار مجاز", owner_phone="09129996002")
    members = owner_membership(seller), owner_membership(buyer)
    for business, member in zip((seller, buyer), members, strict=True):
        install_signatures(business, member)
    item = make_item(seller, available_sqm="100")
    invoice_lines = lines()
    invoice_lines[0]["item"] = item
    invoice = create_partner_draft(
        business=seller, membership=members[0], buyer_business=buyer,
        lines=invoice_lines, credit_amount=Decimal("100"),
    )
    return invoice, *members


def restrict(business, condition):
    fields = {
        "suspended": {"status": Business.Status.SUSPENDED},
        "expired": {"active_until": timezone.localdate() - timedelta(days=1)},
        "browse": {"plan": Business.Plan.BROWSE},
        "unverified": {"verification_status": Business.VerificationStatus.PENDING},
    }[condition]
    # Deliberately leave the caller's Business/membership caches stale.
    Business.objects.filter(pk=business.pk).update(**fields)


def state(invoice, media_root):
    return {
        "invoice": SalesInvoice.objects.filter(pk=invoice.pk).values().get(),
        "revisions": list(InvoiceRevision.objects.filter(invoice=invoice).order_by("pk").values()),
        "sequences": list(Business.objects.order_by("pk").values_list("pk", "invoice_sequence")),
        "financial": [list(model.objects.order_by("pk").values()) for model in (
            Trade, LedgerEntry, SettlementEvent, ChequeReceivable, ChequeEvent,
        )],
        "inventory": list(invoice.seller_business.lots.order_by("pk").values_list("pk", "available_sqm")),
        "files": sorted(str(path.relative_to(media_root)) for path in media_root.rglob("*") if path.is_file()),
    }


@pytest.mark.parametrize("side,condition", [
    ("buyer", "suspended"), ("buyer", "expired"), ("buyer", "unverified"),
    ("seller", "suspended"), ("seller", "expired"), ("seller", "browse"), ("seller", "unverified"),
])
@pytest.mark.parametrize("via_http", [False, True])
def test_confirmation_rechecks_businesses_without_any_side_effects(client, pair, tmp_path, side, condition, via_http):
    invoice, seller, buyer = pair
    invoice = send_partner_invoice(invoice=invoice, membership=seller)
    restrict((buyer if side == "buyer" else seller).business, condition)
    before = state(invoice, tmp_path)
    if via_http:
        client.force_login(buyer.user)
        page = client.get(reverse("invoicing:detail", args=[invoice.pk]))
        assert page.status_code == 200
        assert page.context["can_confirm"] is False
        response = client.post(reverse("invoicing:confirm", args=[invoice.pk]), {"revision_number": "1"})
        assert response.status_code == 302
    else:
        with pytest.raises(InvoiceError):
            confirm_partner_invoice(invoice=invoice, membership=buyer, expected_revision=1)
    assert state(invoice, tmp_path) == before


@pytest.mark.parametrize("side,condition", [
    ("seller", "suspended"), ("seller", "expired"), ("seller", "browse"),
    ("buyer", "suspended"), ("buyer", "unverified"),
])
def test_send_checks_current_businesses_before_snapshotting(pair, tmp_path, side, condition):
    invoice, seller, buyer = pair
    restrict((seller if side == "seller" else buyer).business, condition)
    before = state(invoice, tmp_path)
    with pytest.raises(InvoiceError):
        send_partner_invoice(invoice=invoice, membership=seller)
    assert state(invoice, tmp_path) == before


def test_operational_browse_buyer_can_set_up_signatures_and_confirm_once(client, pair):
    invoice, seller, buyer = pair
    restrict(buyer.business, "browse")
    invoice = send_partner_invoice(invoice=invoice, membership=seller)
    client.force_login(buyer.user)
    assert client.get(reverse("invoicing:settings")).status_code == 200
    for _ in range(2):
        client.post(reverse("invoicing:confirm", args=[invoice.pk]), {"revision_number": "1"})
    invoice.refresh_from_db()
    assert invoice.status == SalesInvoice.Status.CONFIRMED
    assert LedgerEntry.objects.filter(related_invoice=invoice).count() == 2
    assert Trade.objects.count() == SettlementEvent.objects.count() == 1
    assert invoice.items.get().item.available_sqm == Decimal("100")


@pytest.mark.parametrize("allowed", [False, True])
def test_sending_capability_does_not_require_draft_editing_permission(client, pair, allowed):
    invoice, seller, _ = pair
    seller.role = "viewer"
    seller.permissions = ["invoice.view"] + (["invoice.send"] if allowed else [])
    seller.save(update_fields=["role", "permissions"])
    client.force_login(seller.user)
    page = client.get(reverse("invoicing:detail", args=[invoice.pk]))
    assert page.context["can_send"] is allowed
    client.post(reverse("invoicing:issue", args=[invoice.pk]))
    invoice.refresh_from_db()
    assert invoice.status == (SalesInvoice.Status.AWAITING_CONFIRMATION if allowed else SalesInvoice.Status.DRAFT)
    assert not LedgerEntry.objects.exists() and not Trade.objects.exists()


@pytest.mark.parametrize("condition", ["suspended", "expired", "browse"])
def test_local_offline_finalization_checks_seller_before_copying_signatures(pair, tmp_path, condition):
    invoice, seller, _ = pair
    local = resolve_local_counterparty(business=seller.business, membership=seller, name="همکار محلی")
    invoice = update_partner_draft(
        invoice=invoice, membership=seller, buyer_business=None, local_counterparty=local,
        lines=lines(), credit_amount=Decimal("100"),
    )
    restrict(seller.business, condition)
    before = state(invoice, tmp_path)
    with pytest.raises(InvoiceError):
        confirm_local_invoice_offline(
            invoice=invoice, membership=seller, signer_name="نماینده", confirmed_at=timezone.now(),
            signature=signature("offline.png"), attested=True,
        )
    assert state(invoice, tmp_path) == before


@pytest.mark.parametrize("action", ["reject", "cancel"])
def test_active_actor_can_unwind_pending_invoice_with_suspended_counterparty(client, pair, action):
    invoice, seller, buyer = pair
    invoice = send_partner_invoice(invoice=invoice, membership=seller)
    actor, counterparty = (buyer, seller) if action == "reject" else (seller, buyer)
    restrict(counterparty.business, "suspended")
    client.force_login(actor.user)
    page = client.get(reverse("invoicing:detail", args=[invoice.pk]))
    assert page.context["can_reject" if action == "reject" else "can_cancel_pending"]
    command = reject_partner_invoice if action == "reject" else cancel_pending_partner_invoice
    command(invoice=invoice, membership=actor, reason="اصلاح", expected_revision=1)
    assert not LedgerEntry.objects.exists() and not Trade.objects.exists()


def test_offline_approval_capability_authorizes_its_own_ledger_consequence(pair):
    invoice, seller, _ = pair
    local = resolve_local_counterparty(business=seller.business, membership=seller, name="همکار محلی")
    invoice = update_partner_draft(
        invoice=invoice, membership=seller, buyer_business=None, local_counterparty=local,
        lines=lines(), credit_amount=100,
    )
    seller.role, seller.permissions = "viewer", ["invoice.view", "invoice.offline_approve"]
    seller.save(update_fields=["role", "permissions"])
    for _ in range(2):
        confirm_local_invoice_offline(
            invoice=invoice, membership=seller, signer_name="نماینده", confirmed_at=timezone.now(),
            signature=signature("offline.png"), attested=True,
        )
    assert LedgerEntry.objects.filter(related_invoice=invoice).count() == 1
    assert Trade.objects.count() == 1


@pytest.mark.parametrize("wrong_business", [False, True])
def test_invoice_posting_rejects_wrong_actor_or_unrelated_trade_capability(pair, tmp_path, wrong_business):
    invoice, seller, buyer = pair
    invoice = send_partner_invoice(invoice=invoice, membership=seller)
    invoice = confirm_partner_invoice(invoice=invoice, membership=buyer, expected_revision=1)
    actor = seller if wrong_business else buyer
    if not wrong_business:
        actor.role, actor.permissions = "viewer", ["trade.confirm"]
        actor.save(update_fields=["role", "permissions"])
    before = state(invoice, tmp_path)
    with pytest.raises(LedgerError):
        post_invoice_entries(invoice=invoice, membership=actor)
    assert state(invoice, tmp_path) == before


@pytest.mark.parametrize("condition", ["suspended", "expired"])
@pytest.mark.parametrize("action", ["reject", "cancel"])
def test_nonoperational_actor_cannot_decide_or_cancel(pair, tmp_path, condition, action):
    invoice, seller, buyer = pair
    invoice = send_partner_invoice(invoice=invoice, membership=seller)
    actor = buyer if action == "reject" else seller
    restrict(actor.business, condition)
    before = state(invoice, tmp_path)
    command = reject_partner_invoice if action == "reject" else cancel_pending_partner_invoice
    with pytest.raises(InvoiceError):
        command(invoice=invoice, membership=actor, reason="اصلاح", expected_revision=1)
    assert state(invoice, tmp_path) == before


@pytest.mark.parametrize("actor_restricted", [False, True])
def test_cheque_updates_block_suspended_actor_but_allow_historical_counterparties(pair, tmp_path, actor_restricted):
    invoice, seller, buyer = pair
    invoice = update_partner_draft(
        invoice=invoice, membership=seller, lines=lines(), credit_amount=0, cheque_amount=100,
        settlement_method="cheque", cheque_details={"reference_number": "123", "due_date": "2026-12-01"},
    )
    invoice = send_partner_invoice(invoice=invoice, membership=seller)
    confirm_partner_invoice(invoice=invoice, membership=buyer, expected_revision=1)
    restrict((seller if actor_restricted else buyer).business, "suspended")
    cheque = ChequeReceivable.objects.get(invoice=invoice)
    before = state(invoice, tmp_path)
    if actor_restricted:
        with pytest.raises(InvoiceError):
            change_cheque_status(cheque=cheque, membership=seller, status="bounced")
        assert state(invoice, tmp_path) == before
    else:
        for _ in range(2):
            change_cheque_status(cheque=cheque, membership=seller, status="bounced")
        assert ChequeEvent.objects.filter(cheque=cheque, to_status="bounced").count() == 1


@pytest.mark.parametrize("action", ["create", "propose", "cancel", "approve"])
@pytest.mark.parametrize("condition", ["suspended", "expired"])
def test_counterparty_mutations_require_operational_actor(pair, action, condition):
    _, seller, buyer = pair
    local = resolve_local_counterparty(business=seller.business, membership=seller, name="همکار محلی")
    proposal = propose_counterparty_link(local_counterparty=local, target=buyer.business, membership=seller)
    actor = buyer if action == "approve" else seller
    restrict(actor.business, condition)
    commands = {
        "create": lambda: resolve_local_counterparty(business=seller.business, membership=seller, name="جدید"),
        "propose": lambda: propose_counterparty_link(
            local_counterparty=local, target=buyer.business, membership=seller,
        ),
        "cancel": lambda: cancel_counterparty_link(proposal=proposal, membership=seller),
        "approve": lambda: decide_counterparty_link(proposal=proposal, membership=buyer, approve=True),
    }
    with pytest.raises(InvoiceError):
        commands[action]()
    assert LocalCounterparty.objects.count() == CounterpartyLinkProposal.objects.count() == 1
    local.refresh_from_db()
    proposal.refresh_from_db()
    assert local.linked_business_id is None and proposal.status == "pending"
    assert not LedgerEntry.objects.exists()


@pytest.mark.concurrency
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("side", ["seller", "buyer"])
def test_confirmation_rechecks_eligibility_after_waiting_for_business_lock(pair, side):
    invoice, seller, buyer = pair
    invoice = send_partner_invoice(invoice=invoice, membership=seller)
    target_id = (seller if side == "seller" else buyer).business_id
    waiting = threading.Event()
    results = []

    def confirm():
        def observe_lock(execute, sql, params, many, context):
            if 'FROM "businesses_business"' in sql and "FOR UPDATE" in sql and target_id in params:
                waiting.set()
            return execute(sql, params, many, context)

        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '10s'")
            with connection.execute_wrapper(observe_lock):
                confirm_partner_invoice(invoice=invoice, membership=buyer, expected_revision=1)
        except InvoiceError:
            results.append("refused")
        except Exception as exc:
            results.append(exc)
        finally:
            connection.close()

    worker = threading.Thread(target=confirm, daemon=True)
    try:
        with transaction.atomic():
            Business.objects.select_for_update().get(pk=target_id)
            worker.start()
            assert waiting.wait(10), "confirmation did not reach the locked business"
            Business.objects.filter(pk=target_id).update(status=Business.Status.SUSPENDED)
    finally:
        worker.join(timeout=15)
    assert not worker.is_alive()
    assert results == ["refused"]
    invoice.refresh_from_db()
    assert invoice.status == SalesInvoice.Status.AWAITING_CONFIRMATION
    assert not Trade.objects.exists() and not LedgerEntry.objects.exists() and not SettlementEvent.objects.exists()
