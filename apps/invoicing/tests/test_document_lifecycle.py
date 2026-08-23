"""Who may read a document, and what a total of documents means.

A draft is the seller still deciding — the number, the lines, whether to issue it
at all. It was visible to the buyer, who could read a bill that had not been sent
and watch it change. And the invoice report summed every non-cancelled document,
so a figure labelled «مبلغ فاکتورها» moved while somebody was typing a draft.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.businesses.models import BusinessMembership
from apps.core.testing import make_business, make_item, make_product, make_user, owner_membership
from apps.invoicing.models import SalesInvoice
from apps.invoicing.selectors import get_invoice, invoices_for
from apps.invoicing.services import cancel_invoice, create_manual_invoice, issue_invoice
from apps.pricing.services import ensure_default_tiers
from apps.reporting.reports import DateRange, invoice_summary
from apps.trading.services import record_direct_sale

ALL_TIME = DateRange(None, None)


@pytest.fixture
def shop(db):
    ensure_default_tiers()
    seller = make_business(name="سنگ سند", owner_phone="09431110001")
    buyer = make_business(name="سنگ خریدار سند", owner_phone="09431110002")
    seller.seat_limit = 5
    seller.save(update_fields=["seat_limit"])
    staff = BusinessMembership.objects.create(
        user=make_user("09431110003"),
        business=seller,
        role=BusinessMembership.Role.STAFF,
    )
    item = make_item(
        seller,
        product=make_product(seller, commercial_name="تراورتن سند"),
        lot_code="DL-1",
        b2b="1000000",
    )
    return {
        "seller": seller,
        "buyer": buyer,
        "item": item,
        "owner": owner_membership(seller),
        "staff": staff,
    }


def _sale(shop, membership, quantity="10") -> SalesInvoice:
    trade = record_direct_sale(
        seller_business=shop["seller"],
        membership=membership,
        item=shop["item"],
        quantity_sqm=Decimal(quantity),
        unit_price=Decimal("1000000"),
        buyer_business=shop["buyer"],
    )
    return SalesInvoice.objects.get(trade=trade)


# --- drafts are the seller's alone ------------------------------------------------


@pytest.mark.django_db
def test_a_buyer_cannot_list_a_draft(shop):
    """AUD-021."""
    draft = _sale(shop, shop["staff"])
    assert draft.status == SalesInvoice.Status.DRAFT

    assert list(invoices_for(shop["buyer"])) == []
    assert draft in list(invoices_for(shop["seller"]))


@pytest.mark.django_db
def test_a_buyer_cannot_open_a_draft(shop):
    draft = _sale(shop, shop["staff"])
    assert get_invoice(shop["buyer"], draft.id) is None
    assert get_invoice(shop["seller"], draft.id) is not None


@pytest.mark.django_db
def test_a_buyer_gets_no_page_for_a_draft(client, shop):
    draft = _sale(shop, shop["staff"])
    client.force_login(owner_membership(shop["buyer"]).user)
    session = client.session
    session["current_business_id"] = str(shop["buyer"].id)
    session.save()

    response = client.get(reverse("invoicing:detail", kwargs={"invoice_id": draft.id}))
    assert response.status_code == 302


@pytest.mark.django_db
def test_issuing_a_draft_makes_it_visible_to_the_buyer(shop):
    draft = _sale(shop, shop["staff"])
    issue_invoice(invoice=draft, membership=shop["owner"])
    assert get_invoice(shop["buyer"], draft.id) is not None


@pytest.mark.django_db
def test_a_cancelled_invoice_stays_visible_to_the_buyer(shop):
    """They were sent it. Finding it missing is worse than seeing it voided."""
    invoice = _sale(shop, shop["owner"])
    cancel_invoice(invoice=invoice, membership=shop["owner"], reason="ابطال آزمون")
    assert get_invoice(shop["buyer"], invoice.id) is not None


# --- what the totals mean ----------------------------------------------------------


@pytest.mark.django_db
def test_the_invoice_total_sums_issued_documents_only(shop):
    """AUD-032. Drafts used to be included, so the total moved while somebody
    was still typing."""
    _sale(shop, shop["owner"], quantity="10")  # issued: 10,000,000
    _sale(shop, shop["staff"], quantity="20")  # draft: 20,000,000

    summary = invoice_summary(shop["seller"], ALL_TIME)
    assert summary["total"] == Decimal("10000000.00")
    assert summary["draft_total"] == Decimal("20000000.00")
    assert summary["issued_count"] == 1
    assert summary["draft_count"] == 1
    assert summary["total_count"] == 2


@pytest.mark.django_db
def test_a_cancelled_invoice_is_counted_but_not_totalled(shop):
    invoice = _sale(shop, shop["owner"], quantity="10")
    cancel_invoice(invoice=invoice, membership=shop["owner"], reason="ابطال آزمون")

    summary = invoice_summary(shop["seller"], ALL_TIME)
    assert summary["total"] == Decimal("0.00")
    assert summary["cancelled_count"] == 1
    assert summary["total_count"] == 1


# --- numbering ---------------------------------------------------------------------


@pytest.mark.django_db
def test_numbers_stay_sequential_and_are_never_reused(shop):
    """AUD-022. The counter only moves forward, so cancelling never frees a
    number — a gap in the sequence means something."""
    first = _sale(shop, shop["owner"], quantity="1")
    cancel_invoice(invoice=first, membership=shop["owner"], reason="ابطال آزمون")
    second = _sale(shop, shop["owner"], quantity="2")
    third = _sale(shop, shop["owner"], quantity="3")

    assert [int(inv.number) for inv in (first, second, third)] == [1, 2, 3]


@pytest.mark.django_db
def test_the_counter_starts_from_the_existing_history(shop):
    """The migration seeds it, so an upgraded Business does not reissue numbers
    that already exist."""
    _sale(shop, shop["owner"], quantity="1")
    shop["seller"].refresh_from_db()
    assert shop["seller"].invoice_sequence == 1


@pytest.mark.django_db
def test_two_sellers_number_independently(shop):
    other = make_business(name="سنگ دیگر سند", owner_phone="09431110009")
    mine = _sale(shop, shop["owner"])
    theirs = create_manual_invoice(
        business=other,
        membership=owner_membership(other),
        lines=[{"product_name": "سنگ", "quantity": Decimal("1"), "unit_price": Decimal("1"), "item": None}],
        customer_name="مشتری",
    )
    assert mine.number == theirs.number
