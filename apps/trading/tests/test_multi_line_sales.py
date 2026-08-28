"""One sale, several stones.

A stone seller sells a colleague 100 m² of travertine, 70 m² of another
travertine and 50 m² of marble in one phone call. Recording that as three sales
produced three invoices, three ledger entries and three balances to reconcile —
so the workaround for a modelling gap made the bookkeeping worse than the gap.

What this file pins: one Trade, one total, one entry in each party's book, one
invoice with a row per stone, and lines that stay true after the products they
name are renamed or deleted.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from apps.accounting.models import LedgerEntry
from apps.accounting.selectors import current_balance
from apps.core.testing import make_business, make_item, make_product, owner_membership
from apps.inventory.models import InventoryLot
from apps.invoicing.models import SalesInvoice
from apps.pricing.services import ensure_default_tiers
from apps.reporting.reports import DateRange, sales_by_product, sales_by_stone_type, sales_summary
from apps.trading.models import Trade, TradeItem
from apps.trading.services import TradingError, record_direct_sale

WINDOW = DateRange()


@pytest.fixture
def market(db):
    ensure_default_tiers()
    seller = make_business(name="سنگ فروشنده چندقلم", owner_phone="09191110001")
    buyer = make_business(name="سنگ خریدار چندقلم", owner_phone="09191110002")
    travertine = make_item(
        seller,
        product=make_product(seller, commercial_name="تراورتن عباس‌آباد", stone_type="تراورتن"),
        lot_code="ML-1",
        b2b="1500000",
    )
    dareh = make_item(
        seller,
        product=make_product(seller, commercial_name="تراورتن دره بخاری", stone_type="تراورتن"),
        lot_code="ML-2",
        b2b="1200000",
    )
    marble = make_item(
        seller,
        product=make_product(seller, commercial_name="مرمریت لاشتر", stone_type="مرمریت"),
        lot_code="ML-3",
        b2b="2000000",
    )
    return {
        "seller": seller,
        "buyer": buyer,
        "seller_m": owner_membership(seller),
        "travertine": travertine,
        "dareh": dareh,
        "marble": marble,
    }


def _three_line_sale(market, **kwargs) -> Trade:
    params = {
        "seller_business": market["seller"],
        "membership": market["seller_m"],
        "buyer_business": market["buyer"],
        "lines": [
            {"item": market["travertine"], "quantity": "100", "unit_price": "1500000"},
            {"item": market["dareh"], "quantity": "70", "unit_price": "1200000"},
            {"item": market["marble"], "quantity": "50", "unit_price": "2000000"},
        ],
    }
    params.update(kwargs)
    return record_direct_sale(**params)


#: 100×1.5m + 70×1.2m + 50×2m
EXPECTED_TOTAL = Decimal("334000000.00")


# --- one commercial event -----------------------------------------------------


def test_a_three_stone_order_is_one_trade_with_three_lines(market):
    trade = _three_line_sale(market)

    assert Trade.objects.count() == 1
    assert trade.items.count() == 3
    assert [line.product_name for line in trade.items.all()] == [
        "سنگ تراورتن عباس‌آباد",
        "سنگ تراورتن دره بخاری",
        "سنگ مرمریت لاشتر",
    ]


def test_the_total_is_the_sum_of_the_lines(market):
    trade = _three_line_sale(market)

    assert trade.total_amount == EXPECTED_TOTAL
    assert sum(line.line_total for line in trade.items.all()) == EXPECTED_TOTAL


def test_a_three_stone_order_moves_each_book_exactly_once(market):
    trade = _three_line_sale(market)

    sale_entries = LedgerEntry.objects.filter(related_trade=trade, entry_type=LedgerEntry.Type.SALE)
    purchase_entries = LedgerEntry.objects.filter(
        related_trade=trade, entry_type=LedgerEntry.Type.PURCHASE
    )
    assert sale_entries.count() == 1, "three stones are one debt, not three"
    assert purchase_entries.count() == 1
    assert sale_entries.get().amount == EXPECTED_TOTAL
    assert current_balance(market["seller"], market["buyer"]) == EXPECTED_TOTAL
    assert current_balance(market["buyer"], market["seller"]) == -EXPECTED_TOTAL


def test_a_three_stone_order_produces_one_invoice_with_three_rows(market):
    trade = _three_line_sale(market)

    invoice = SalesInvoice.objects.get(trade=trade)
    assert invoice.total_amount == EXPECTED_TOTAL
    assert invoice.items.count() == 3
    assert [line.product_name for line in invoice.items.all()] == [
        "سنگ تراورتن عباس‌آباد",
        "سنگ تراورتن دره بخاری",
        "سنگ مرمریت لاشتر",
    ]
    assert sum(line.line_total for line in invoice.items.all()) == invoice.total_amount


def test_the_statement_describes_the_sale_by_its_size_not_by_one_stone(market):
    """Naming a three-stone sale after whichever stone happened to be first would
    describe the entry wrongly."""
    trade = _three_line_sale(market)

    entry = LedgerEntry.objects.get(related_trade=trade, entry_type=LedgerEntry.Type.SALE)
    assert "3 قلم" in entry.description
    assert "تراورتن عباس‌آباد" not in entry.description


def test_a_single_line_sale_is_still_described_by_its_product(market):
    trade = record_direct_sale(
        seller_business=market["seller"],
        membership=market["seller_m"],
        buyer_business=market["buyer"],
        item=market["marble"],
        quantity_sqm="50",
        unit_price="2000000",
    )

    entry = LedgerEntry.objects.get(related_trade=trade, entry_type=LedgerEntry.Type.SALE)
    assert "مرمریت لاشتر" in entry.description


# --- rounding -----------------------------------------------------------------


def test_each_line_is_rounded_before_the_lines_are_summed(market):
    """Summing first and rounding once would make the invoice's own rows fail to
    add up to the total printed at the bottom of it."""
    trade = record_direct_sale(
        seller_business=market["seller"],
        membership=market["seller_m"],
        buyer_business=market["buyer"],
        lines=[
            {"item": market["travertine"], "quantity": "0.333", "unit_price": "1000001"},
            {"item": market["marble"], "quantity": "0.333", "unit_price": "1000001"},
        ],
    )

    lines = list(trade.items.all())
    assert all(line.line_total == line.line_total.quantize(Decimal("0.01")) for line in lines)
    assert trade.total_amount == sum(line.line_total for line in lines)

    invoice = SalesInvoice.objects.get(trade=trade)
    assert invoice.total_amount == sum(line.line_total for line in invoice.items.all())


# --- history stays true -------------------------------------------------------


def test_renaming_a_product_does_not_rewrite_a_sold_line(market):
    trade = _three_line_sale(market)

    product = market["marble"].product
    from apps.inventory.models import VocabularyTerm

    product.name_suffix = "یک نام کاملاً متفاوت"
    product.stone = VocabularyTerm.objects.get(name="گرانیت")
    product.save()

    line = trade.items.get(item=market["marble"])
    assert line.product_name == "سنگ مرمریت لاشتر"
    assert line.stone_type == "مرمریت"


def test_deleting_a_product_leaves_the_sold_line_readable(market):
    trade = _three_line_sale(market)
    lot_id = market["marble"].id

    InventoryLot.objects.filter(pk=lot_id).delete()

    line = trade.items.get(product_name="سنگ مرمریت لاشتر")
    assert line.item_id is None, "the navigation link goes, the history stays"
    assert line.quantity == Decimal("50.000")
    assert line.line_total == Decimal("100000000.00")
    assert Trade.objects.get(pk=trade.pk).total_amount == EXPECTED_TOTAL


# --- idempotency still holds with several lines -------------------------------


def test_resubmitting_a_multi_line_sale_records_it_once(market):
    token = uuid.uuid4()

    first = _three_line_sale(market, submission_id=token)
    second = _three_line_sale(market, submission_id=token)

    assert first.pk == second.pk
    assert Trade.objects.count() == 1
    assert TradeItem.objects.count() == 3
    assert SalesInvoice.objects.count() == 1
    assert current_balance(market["seller"], market["buyer"]) == EXPECTED_TOTAL


# --- reports ------------------------------------------------------------------


def test_reports_split_a_multi_stone_sale_across_its_stone_types(market):
    _three_line_sale(market)

    rows = {row["name"]: row for row in sales_by_stone_type(market["seller"], WINDOW)}
    assert set(rows) == {"تراورتن", "مرمریت"}
    assert rows["تراورتن"]["total"] == Decimal("234000000.00")
    assert rows["مرمریت"]["total"] == Decimal("100000000.00")
    assert sum(row["total"] for row in rows.values()) == EXPECTED_TOTAL


def test_reports_never_multiply_the_money_by_the_number_of_lines(market):
    """The fan-out this refactor could easily have introduced: summing the header
    total across a join to the lines counts a three-line sale three times."""
    _three_line_sale(market)

    summary = sales_summary(market["seller"], WINDOW)
    assert summary["total"] == EXPECTED_TOTAL
    assert summary["trade_count"] == 1
    assert summary["quantity_sqm"] == Decimal("220.000")


def test_sales_by_product_counts_a_multi_line_sale_once_per_product(market):
    _three_line_sale(market)

    rows = {row["name"]: row for row in sales_by_product(market["seller"], WINDOW)}
    assert len(rows) == 3
    assert all(row["trade_count"] == 1 for row in rows.values())
    assert sum(row["total"] for row in rows.values()) == EXPECTED_TOTAL


# --- validation ---------------------------------------------------------------


def test_a_sale_with_no_lines_is_refused(market):
    with pytest.raises(TradingError):
        record_direct_sale(
            seller_business=market["seller"],
            membership=market["seller_m"],
            buyer_business=market["buyer"],
            lines=[],
        )


def test_a_line_naming_another_business_product_is_refused(market):
    stranger = make_business(name="سنگ غریبه", owner_phone="09191110003")
    theirs = make_item(stranger, lot_code="ML-9", b2b="1000000")

    with pytest.raises(TradingError):
        record_direct_sale(
            seller_business=market["seller"],
            membership=market["seller_m"],
            buyer_business=market["buyer"],
            lines=[
                {"item": market["marble"], "quantity": "10", "unit_price": "1000000"},
                {"item": theirs, "quantity": "10", "unit_price": "1000000"},
            ],
        )

    assert Trade.objects.count() == 0
