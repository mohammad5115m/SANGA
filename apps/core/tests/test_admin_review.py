"""Direct-request regressions for the platform console review."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib import admin
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.businesses.models import BusinessMembership
from apps.core.testing import make_business, owner_membership
from apps.inquiries.models import CustomerFollowUp, CustomerLead
from apps.invoicing.models import (
    ChequeReceivable,
    CounterpartyLinkProposal,
    InvoiceRevision,
    LocalCounterparty,
    SalesInvoice,
    SalesInvoiceItem,
    SettlementEvent,
)


@pytest.fixture
def console(client, db):
    user = User.objects.create_superuser(phone="09129998001", password="test-only")
    client.force_login(user)
    return client


@pytest.fixture
def seller(db):
    return make_business(name="سنگ بازبینی", owner_phone="09129998002")


def test_membership_reactivation_respects_capacity(console, seller):
    user = User.objects.create_user(phone="09129998003")
    member = BusinessMembership.objects.create(
        business=seller, user=user, status=BusinessMembership.Status.SUSPENDED,
    )
    seller.seat_limit = 1
    seller.save(update_fields=["seat_limit"])
    response = console.post(reverse("admin:businesses_businessmembership_change", args=[member.pk]), {
        "status": BusinessMembership.Status.ACTIVE, "_save": "1",
    })
    member.refresh_from_db()
    assert member.status == BusinessMembership.Status.SUSPENDED
    assert response.status_code == 200
    assert "سقف کاربران" in response.content.decode()

    seller.seat_limit = 2
    seller.save(update_fields=["seat_limit"])
    assert console.post(reverse("admin:businesses_businessmembership_change", args=[member.pk]), {
        "status": BusinessMembership.Status.ACTIVE, "_save": "1",
    }).status_code == 302
    member.refresh_from_db()
    assert member.status == BusinessMembership.Status.ACTIVE
    # Saving an already-active member and suspending one remain possible after
    # lowering capacity; neither operation allocates a new seat.
    seller.seat_limit = 1
    seller.save(update_fields=["seat_limit"])
    for status in (BusinessMembership.Status.ACTIVE, BusinessMembership.Status.SUSPENDED):
        assert console.post(reverse("admin:businesses_businessmembership_change", args=[member.pk]), {
            "status": status, "_save": "1",
        }).status_code == 302
        member.refresh_from_db()
        assert member.status == status


def test_user_delete_cannot_remove_owner_membership(console, seller):
    owner = owner_membership(seller).user
    response = console.post(reverse("admin:accounts_user_delete", args=[owner.pk]), {"post": "yes"})
    assert response.status_code == 403
    assert seller.memberships.filter(user=owner).exists()
    response = console.post(reverse("admin:accounts_user_changelist"), {
        "action": "delete_selected", "_selected_action": str(owner.pk), "post": "yes",
    })
    assert response.status_code in (200, 302, 403)
    assert User.objects.filter(pk=owner.pk).exists()


@pytest.fixture
def financial_records(seller):
    actor = owner_membership(seller).user
    invoice = SalesInvoice.objects.create(
        seller_business=seller, issue_date=timezone.localdate(), counterparty_type="customer",
    )
    line = SalesInvoiceItem.objects.create(
        invoice=invoice, product_name="سنگ تاریخی", quantity=1, unit_price=100,
        gross_total=100, line_total=100,
    )
    revision = InvoiceRevision.objects.create(
        invoice=invoice, revision_number=1, sent_by=actor, sent_at=timezone.now(),
    )
    event = SettlementEvent.objects.create(
        invoice=invoice, revision=revision, kind="cheque", amount=100, currency="IRR",
        idempotency_key="admin-review", recorded_by=actor, occurred_at=timezone.now(),
    )
    cheque = ChequeReceivable.objects.create(
        invoice=invoice, settlement_event=event, amount=100, currency="IRR",
        reference_number="REVIEW-1", due_date=timezone.localdate(),
    )
    local = LocalCounterparty.objects.create(owner_business=seller, name="همکار محلی")
    buyer = make_business(name="خریدار بازبینی", owner_phone="09129998004")
    link = CounterpartyLinkProposal.objects.create(local_counterparty=local, target_business=buyer, proposed_by=actor)
    return invoice, line, revision, event, cheque, local, link


@pytest.mark.parametrize("record_index", [0, 2, 3, 4, 5, 6])
def test_financial_admin_is_readable_but_refuses_direct_writes(console, financial_records, record_index):
    obj = financial_records[record_index]
    opts = obj._meta
    base = f"admin:{opts.app_label}_{opts.model_name}"
    change_url = reverse(base + "_change", args=[obj.pk])
    response = console.get(change_url)
    assert response.status_code == 200
    assert 'name="_save"' not in response.content.decode()
    assert console.post(change_url, {"status": "cleared", "_save": "1"}).status_code == 403
    assert console.post(reverse(base + "_add"), {}).status_code == 403
    assert console.post(reverse(base + "_delete", args=[obj.pk]), {"post": "yes"}).status_code == 403


def test_invoice_inline_rejects_tampering(console, financial_records):
    invoice, line, *_ = financial_records
    url = reverse("admin:invoicing_salesinvoice_change", args=[invoice.pk])
    response = console.post(url, {
        "status": "issued", "_save": "1",
        "items-TOTAL_FORMS": "2", "items-INITIAL_FORMS": "1",
        "items-0-id": str(line.pk), "items-0-description": "tampered",
        "items-0-discount_value": "90", "items-0-DELETE": "on",
        "items-1-product_name": "injected", "items-1-quantity": "1", "items-1-unit_price": "50",
    })
    assert response.status_code == 403
    line.refresh_from_db()
    invoice.refresh_from_db()
    assert line.description == "" and line.discount_value == Decimal("0")
    assert invoice.status == SalesInvoice.Status.DRAFT and invoice.items.count() == 1
    model_admin = admin.site._registry[SalesInvoice]
    request = response.wsgi_request
    inline = model_admin.inlines[0](SalesInvoice, admin.site)
    assert not inline.has_add_permission(request, invoice)
    assert not inline.has_change_permission(request, invoice)
    assert not inline.has_delete_permission(request, invoice)


def test_dashboard_links_match_their_counts(console, seller):
    customer = CustomerLead.objects.create(business=seller, phone="09129998005", name="مشتری پیگیری")
    now = timezone.now()
    for status, scheduled in [("scheduled", now - timedelta(days=1)), ("scheduled", now + timedelta(days=1)),
                              ("completed", now - timedelta(days=1)), ("postponed", now - timedelta(days=1))]:
        CustomerFollowUp.objects.create(
            customer=customer, business=seller, title="پیگیری", status=status,
            scheduled_for=scheduled, remind_at=scheduled,
        )
    response = console.get(reverse("admin:index"))
    for card in response.context["admin_stats"] + response.context["admin_queue"]:
        destination = console.get(card["url"])
        assert destination.status_code == 200, card
        assert destination.context["cl"].queryset.count() == card.get("value", card.get("count")), card
