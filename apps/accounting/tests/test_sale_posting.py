"""Finalizing a sale is the one event that moves the books.

Two rules under test: it must happen exactly once per trade, and issuing or
printing the invoice afterwards must never post again. A business whose every
sale is counted twice has no usable books at all.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.accounting.models import LedgerEntry
from apps.accounting.selectors import current_balance
from apps.accounting.services import LedgerDuplicateError, post_trade_for_sale, reverse_entry
from apps.businesses.models import BusinessMembership
from apps.core.testing import make_business, make_item, make_product, make_user, owner_membership
from apps.invoicing.models import SalesInvoice
from apps.invoicing.services import cancel_invoice, create_invoice_for_trade, issue_invoice
from apps.pricing.services import ensure_default_tiers
from apps.trading.services import create_purchase_request, finalize_sale, respond_to_purchase_request


@pytest.fixture
def sale(db):
    ensure_default_tiers()
    seller = make_business(name="سنگ فروشنده", owner_phone="09191110001")
    buyer = make_business(name="سنگ خریدار", owner_phone="09191110002")
    seller.seat_limit = 5
    seller.save(update_fields=["seat_limit"])

    item = make_item(
        seller,
        product=make_product(seller, commercial_name="تراورتن عباس‌آباد"),
        lot_code="SL-1",
        b2b="1000000",
    )
    seller_m = owner_membership(seller)
    buyer_m = owner_membership(buyer)

    request_ = create_purchase_request(
        buyer_business=buyer,
        membership=buyer_m,
        item=item,
        requested_qty_sqm=Decimal("50"),
        proposed_unit_price=Decimal("1000000"),
    )
    respond_to_purchase_request(request=request_, membership=seller_m, accept=True)
    return {
        "seller": seller,
        "buyer": buyer,
        "seller_m": seller_m,
        "buyer_m": buyer_m,
        "item": item,
        "request": request_,
    }


# --- finalizing posts exactly once --------------------------------------------


@pytest.mark.django_db
def test_finalizing_a_sale_moves_the_colleague_balance(sale):
    finalize_sale(request=sale["request"], membership=sale["seller_m"])

    balance = current_balance(sale["seller"], sale["buyer"])
    assert balance == Decimal("50000000.00")

    entry = LedgerEntry.objects.get(business=sale["seller"], entry_type=LedgerEntry.Type.SALE)
    assert entry.counterparty_business_id == sale["buyer"].id
    assert entry.related_trade is not None


@pytest.mark.django_db
def test_finalizing_twice_does_not_double_the_balance(sale):
    finalize_sale(request=sale["request"], membership=sale["seller_m"])
    from apps.trading.services import TradingError

    with pytest.raises(TradingError):
        finalize_sale(request=sale["request"], membership=sale["seller_m"])

    assert LedgerEntry.objects.filter(entry_type=LedgerEntry.Type.SALE).count() == 1
    assert current_balance(sale["seller"], sale["buyer"]) == Decimal("50000000.00")


@pytest.mark.django_db
def test_posting_the_same_trade_twice_is_refused(sale):
    trade = finalize_sale(request=sale["request"], membership=sale["seller_m"])
    with pytest.raises(LedgerDuplicateError):
        post_trade_for_sale(trade=trade, membership=sale["seller_m"])
    assert LedgerEntry.objects.filter(entry_type=LedgerEntry.Type.SALE).count() == 1


@pytest.mark.django_db
def test_the_database_itself_rejects_a_second_live_trade_entry(sale):
    """Belt and braces: the service pre-check is not the only thing stopping it."""
    from django.db import IntegrityError, transaction

    trade = finalize_sale(request=sale["request"], membership=sale["seller_m"])
    with pytest.raises(IntegrityError), transaction.atomic():
        LedgerEntry.objects.create(
            business=sale["seller"],
            counterparty_business=sale["buyer"],
            entry_type=LedgerEntry.Type.SALE,
            amount=Decimal("1"),
            balance_delta=Decimal("1"),
            balance_after=Decimal("1"),
            occurred_on=trade.finalized_at.date(),
            related_trade=trade,
        )


@pytest.mark.django_db
def test_a_reversal_frees_the_slot_so_a_corrected_sale_can_be_reposted(sale):
    trade = finalize_sale(request=sale["request"], membership=sale["seller_m"])
    entry = LedgerEntry.objects.get(entry_type=LedgerEntry.Type.SALE)

    reverse_entry(entry=entry, membership=sale["seller_m"])
    assert current_balance(sale["seller"], sale["buyer"]) == Decimal("0.00")

    reposted = post_trade_for_sale(trade=trade, membership=sale["seller_m"])
    assert reposted is not None
    assert current_balance(sale["seller"], sale["buyer"]) == Decimal("50000000.00")


@pytest.mark.django_db
def test_a_salesperson_can_finalize_without_ledger_manage(sale):
    """Posting is a consequence of the sale, not bookkeeping the user authored.

    Requiring ledger.manage here would mean no salesperson could complete a sale.
    """
    staff = BusinessMembership.objects.create(
        user=make_user("09191110009"),
        business=sale["seller"],
        role=BusinessMembership.Role.STAFF,
        status=BusinessMembership.Status.ACTIVE,
    )
    assert staff.has_capability("sale.finalize")
    assert not staff.has_capability("ledger.manage")

    finalize_sale(request=sale["request"], membership=staff)
    assert current_balance(sale["seller"], sale["buyer"]) == Decimal("50000000.00")


@pytest.mark.django_db
def test_a_direct_customer_sale_posts_no_colleague_entry(sale):
    """There is no colleague account to move, and inventing one would create a
    debtor nobody can settle with."""
    from apps.trading.services import record_direct_sale

    trade = record_direct_sale(
        seller_business=sale["seller"],
        membership=sale["seller_m"],
        item=sale["item"],
        quantity_sqm=Decimal("10"),
        unit_price=Decimal("2000000"),
        customer_name="آقای رضایی",
    )
    assert post_trade_for_sale(trade=trade, membership=sale["seller_m"]) is None
    assert not LedgerEntry.objects.exists()


# --- invoices never post ------------------------------------------------------


@pytest.mark.django_db
def test_finalizing_creates_an_invoice_alongside_the_entry(sale):
    trade = finalize_sale(request=sale["request"], membership=sale["seller_m"])

    invoice = SalesInvoice.objects.get(trade=trade)
    assert invoice.total_amount == Decimal("50000000.00")
    assert invoice.buyer_name == "سنگ خریدار"
    assert LedgerEntry.objects.filter(entry_type=LedgerEntry.Type.SALE).count() == 1


@pytest.mark.django_db
def test_issuing_the_invoice_does_not_post_again(sale):
    trade = finalize_sale(request=sale["request"], membership=sale["seller_m"])
    invoice = SalesInvoice.objects.get(trade=trade)
    balance_before = current_balance(sale["seller"], sale["buyer"])

    issue_invoice(invoice=invoice, membership=sale["seller_m"])
    issue_invoice(invoice=invoice, membership=sale["seller_m"])

    assert LedgerEntry.objects.filter(entry_type=LedgerEntry.Type.SALE).count() == 1
    assert current_balance(sale["seller"], sale["buyer"]) == balance_before


@pytest.mark.django_db
def test_cancelling_an_invoice_does_not_change_the_balance(sale):
    """Voiding a document and reversing money are separate acts, on purpose."""
    trade = finalize_sale(request=sale["request"], membership=sale["seller_m"])
    invoice = SalesInvoice.objects.get(trade=trade)

    cancel_invoice(invoice=invoice, membership=sale["seller_m"])
    invoice.refresh_from_db()

    assert invoice.status == SalesInvoice.Status.CANCELLED
    assert current_balance(sale["seller"], sale["buyer"]) == Decimal("50000000.00")


@pytest.mark.django_db
def test_asking_for_the_invoice_twice_returns_the_same_document(sale):
    trade = finalize_sale(request=sale["request"], membership=sale["seller_m"])
    first = create_invoice_for_trade(trade=trade, membership=sale["seller_m"])
    second = create_invoice_for_trade(trade=trade, membership=sale["seller_m"])

    assert first.pk == second.pk
    assert SalesInvoice.objects.filter(trade=trade).count() == 1
