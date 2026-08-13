"""Invoices are historical documents.

The property that matters: an invoice issued today still reads correctly after
tomorrow's rename, reprice or deletion. That is the deliberate opposite of a
catalog, which is always live.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.businesses.models import Business
from apps.core.testing import make_business, make_item, make_product, owner_membership
from apps.invoicing.models import SalesInvoice
from apps.invoicing.selectors import get_invoice, invoices_between, invoices_for
from apps.invoicing.services import (
    InvoiceError,
    create_manual_invoice,
    issue_invoice,
)
from apps.pricing.services import ensure_default_tiers
from apps.trading.services import TradingError, record_direct_sale


@pytest.fixture
def shop(db):
    ensure_default_tiers()
    seller = make_business(name="سنگ فروشنده", owner_phone="09201110001")
    colleague = make_business(name="سنگ همکار", owner_phone="09201110002")
    item = make_item(
        seller,
        product=make_product(seller, commercial_name="تراورتن عباس‌آباد", stone_type="تراورتن"),
        lot_code="INV-1",
        grade="سوپر",
        b2b="1500000",
        b2c="2000000",
    )
    return {
        "seller": seller,
        "colleague": colleague,
        "membership": owner_membership(seller),
        "item": item,
    }


def _line(**kwargs) -> dict:
    line = {
        "product_name": "تراورتن عباس‌آباد",
        "stone_type": "تراورتن",
        "grade": "سوپر",
        "quantity": Decimal("40"),
        "unit_price": Decimal("1500000"),
        "item": None,
    }
    line.update(kwargs)
    return line


def _invoice(shop, **kwargs) -> SalesInvoice:
    """A colleague invoice — which now has exactly one origin: a direct sale.

    A sale to another Business moves that colleague's account, so it must be
    backed by a Trade. Typing the document by hand is refused (see
    ``test_a_colleague_invoice_cannot_be_typed_by_hand``).
    """
    params = {
        "seller_business": shop["seller"],
        "membership": shop["membership"],
        "item": shop["item"],
        "quantity_sqm": Decimal("40"),
        "unit_price": Decimal("1500000"),
        "buyer_business": shop["colleague"],
    }
    params.update(kwargs)
    trade = record_direct_sale(**params)
    return SalesInvoice.objects.get(trade=trade)


def _customer_invoice(shop, **kwargs) -> SalesInvoice:
    params = {
        "business": shop["seller"],
        "membership": shop["membership"],
        "lines": [_line()],
        "customer_name": "آقای رضایی",
    }
    params.update(kwargs)
    return create_manual_invoice(**params)


# --- snapshots ----------------------------------------------------------------


@pytest.mark.django_db
def test_an_invoice_keeps_its_own_copy_of_every_line(shop):
    invoice = _invoice(shop)
    line = invoice.items.get()

    assert line.product_name == "تراورتن عباس‌آباد"
    assert line.grade == "سوپر"
    assert line.unit_price == Decimal("1500000.00")
    assert line.line_total == Decimal("60000000.00")
    assert invoice.total_amount == Decimal("60000000.00")


@pytest.mark.django_db
def test_renaming_or_repricing_the_product_does_not_change_the_invoice(shop):
    invoice = _invoice(shop)

    product = shop["item"].product
    product.commercial_name = "نام جدید کاملاً متفاوت"
    product.save(update_fields=["commercial_name"])
    from apps.pricing.services import set_lot_price

    set_lot_price(lot=shop["item"], tier_code="b2b", amount=Decimal("9999999"))

    line = invoice.items.get()
    line.refresh_from_db()
    assert line.product_name == "تراورتن عباس‌آباد"
    assert line.unit_price == Decimal("1500000.00")


@pytest.mark.django_db
def test_deleting_the_product_does_not_change_the_invoice(shop):
    from apps.inventory.services import delete_item

    invoice = _invoice(shop)
    outcome = delete_item(lot=shop["item"], membership=shop["membership"])
    assert outcome == "archived", "a product on an invoice must not be purged"

    line = invoice.items.get()
    line.refresh_from_db()
    assert line.product_name == "تراورتن عباس‌آباد"
    assert line.line_total == Decimal("60000000.00")


@pytest.mark.django_db
def test_the_buyer_name_is_snapshotted_too(shop):
    invoice = _invoice(shop)
    shop["colleague"].name = "نام جدید همکار"
    shop["colleague"].save(update_fields=["name"])

    invoice.refresh_from_db()
    assert invoice.buyer_name == "سنگ همکار"


# --- numbering ----------------------------------------------------------------


@pytest.mark.django_db
def test_numbers_are_sequential_per_seller(shop):
    first = _invoice(shop)
    second = _invoice(shop)
    assert int(first.number) + 1 == int(second.number)


@pytest.mark.django_db
def test_two_sellers_number_independently(shop):
    other = make_business(name="سنگ دیگر", owner_phone="09201110005")
    mine = _invoice(shop)
    theirs = create_manual_invoice(
        business=other,
        membership=owner_membership(other),
        lines=[_line()],
        customer_name="مشتری",
    )
    assert mine.number == theirs.number
    assert SalesInvoice.objects.count() == 2


@pytest.mark.django_db
def test_cancelling_does_not_free_a_number_for_reuse(shop):
    from apps.invoicing.services import cancel_invoice

    first = _invoice(shop)
    cancel_invoice(invoice=first, membership=shop["membership"])
    second = _invoice(shop)
    assert second.number != first.number


# --- counterparties -----------------------------------------------------------


@pytest.mark.django_db
def test_an_invoice_for_a_walk_in_customer_creates_no_user(shop):
    from django.contrib.auth import get_user_model

    before = get_user_model().objects.count()
    invoice = create_manual_invoice(
        business=shop["seller"],
        membership=shop["membership"],
        lines=[_line()],
        customer_name="آقای رضایی",
        customer_phone="09120001111",
    )
    assert invoice.counterparty_type == SalesInvoice.Counterparty.CUSTOMER
    assert invoice.buyer_business_id is None
    assert invoice.buyer_name == "آقای رضایی"
    assert get_user_model().objects.count() == before


@pytest.mark.django_db
def test_an_invoice_needs_a_buyer(shop):
    with pytest.raises(InvoiceError):
        create_manual_invoice(
            business=shop["seller"],
            membership=shop["membership"],
            lines=[_line()],
        )


@pytest.mark.django_db
def test_an_invoice_needs_at_least_one_line(shop):
    with pytest.raises(InvoiceError):
        _customer_invoice(shop, lines=[])


@pytest.mark.django_db
def test_a_business_cannot_invoice_itself(shop):
    with pytest.raises(TradingError):
        _invoice(shop, buyer_business=shop["seller"])


# --- one commercial event, one representation ---------------------------------


@pytest.mark.django_db
def test_a_colleague_invoice_cannot_be_typed_by_hand(shop):
    """The regression this rule exists for.

    Hand-typing a colleague invoice produced a valid-looking document while the
    colleague's balance stayed at zero: one sale, described two ways, only one of
    which counted.
    """
    with pytest.raises(InvoiceError):
        create_manual_invoice(
            business=shop["seller"],
            membership=shop["membership"],
            lines=[_line()],
            buyer_business=shop["colleague"],
        )
    assert not SalesInvoice.objects.exists()


@pytest.mark.django_db
def test_a_direct_colleague_sale_produces_trade_ledger_and_invoice_together(shop):
    from apps.accounting.models import LedgerEntry
    from apps.accounting.selectors import current_balance
    from apps.trading.models import Trade

    invoice = _invoice(shop)

    trade = Trade.objects.get()
    assert invoice.trade_id == trade.id
    assert invoice.total_amount == trade.total_amount == Decimal("60000000.00")
    assert current_balance(shop["seller"], shop["colleague"]) == Decimal("60000000.00")
    assert current_balance(shop["colleague"], shop["seller"]) == Decimal("-60000000.00")
    assert LedgerEntry.objects.filter(related_trade=trade).count() == 2


@pytest.mark.django_db
def test_one_trade_can_never_have_two_invoices(shop):
    """The database says so, not just the service."""
    from django.db import IntegrityError, transaction

    invoice = _invoice(shop)
    with pytest.raises(IntegrityError), transaction.atomic():
        SalesInvoice.objects.create(
            seller_business=shop["seller"],
            number="99999",
            counterparty_type=SalesInvoice.Counterparty.BUSINESS,
            buyer_business=shop["colleague"],
            buyer_name="سنگ همکار",
            trade=invoice.trade,
            issue_date=invoice.issue_date,
            total_amount=Decimal("1"),
        )


# --- visibility ---------------------------------------------------------------


@pytest.mark.django_db
def test_both_parties_can_see_the_invoice(shop):
    invoice = _invoice(shop)
    assert get_invoice(shop["seller"], invoice.id) is not None
    assert get_invoice(shop["colleague"], invoice.id) is not None


@pytest.mark.django_db
def test_a_third_business_cannot_see_it(shop):
    intruder = make_business(name="سنگ غریبه", owner_phone="09201110009")
    invoice = _invoice(shop)
    assert get_invoice(intruder, invoice.id) is None
    assert list(invoices_for(intruder)) == []


@pytest.mark.django_db
def test_invoices_between_two_businesses_covers_both_directions(shop):
    _invoice(shop)
    record_direct_sale(
        seller_business=shop["colleague"],
        membership=owner_membership(shop["colleague"]),
        item=None,
        product_name="گرانیت نطنز",
        quantity_sqm=Decimal("10"),
        unit_price=Decimal("2000000"),
        buyer_business=shop["seller"],
    )
    assert invoices_between(shop["seller"], shop["colleague"]).count() == 2


# --- plan and permissions -----------------------------------------------------


@pytest.mark.django_db
def test_a_browse_only_business_cannot_issue_invoices(shop):
    shop["seller"].plan = Business.Plan.BROWSE
    shop["seller"].save(update_fields=["plan"])
    with pytest.raises(InvoiceError):
        _customer_invoice(shop)


@pytest.mark.django_db
def test_a_cancelled_invoice_cannot_be_issued(shop):
    from apps.invoicing.services import cancel_invoice

    invoice = _invoice(shop)
    cancel_invoice(invoice=invoice, membership=shop["membership"])
    with pytest.raises(InvoiceError):
        issue_invoice(invoice=invoice, membership=shop["membership"])


# --- pages --------------------------------------------------------------------


def _login(client, business):
    client.force_login(business.memberships.get(role="owner").user)
    session = client.session
    session["current_business_id"] = str(business.id)
    session.save()


@pytest.mark.django_db
def test_the_invoice_page_shows_the_snapshot(client, shop):
    invoice = _invoice(shop)
    _login(client, shop["seller"])
    body = client.get(reverse("invoicing:detail", kwargs={"invoice_id": invoice.id})).content.decode()
    assert invoice.number in body
    assert "تراورتن عباس‌آباد" in body
    assert "60,000,000" in body


@pytest.mark.django_db
def test_the_print_page_renders(client, shop):
    invoice = _invoice(shop)
    _login(client, shop["seller"])
    response = client.get(reverse("invoicing:print", kwargs={"invoice_id": invoice.id}))
    assert response.status_code == 200
    assert "فاکتور فروش" in response.content.decode()


@pytest.mark.django_db
def test_a_third_business_gets_no_invoice_page(client, shop):
    intruder = make_business(name="سنگ غریبه", owner_phone="09201110020")
    invoice = _invoice(shop)
    _login(client, intruder)
    response = client.get(reverse("invoicing:detail", kwargs={"invoice_id": invoice.id}))
    assert response.status_code == 302
