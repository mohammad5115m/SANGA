"""The TradeItem backfill preserves every historical trade exactly."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from apps.core.testing import make_business

BEFORE = "0003_trade_items"
AFTER = "0004_backfill_trade_items"

pytestmark = pytest.mark.django_db(transaction=True)


def _migrate(migration: str):
    executor = MigrationExecutor(connection)
    executor.migrate([("trading", migration)])
    return executor.loader.project_state([("trading", migration)]).apps


def _restore_head() -> None:
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())


@pytest.fixture
def before_backfill():
    """Expose the pre-backfill model state and restore the full schema afterward."""
    apps = _migrate(BEFORE)
    yield apps
    _restore_head()


def _legacy_trade(apps, **kwargs):
    """Create a trade through the historical model that matches the old schema."""
    Trade = apps.get_model("trading", "Trade")
    index = Trade.objects.count()
    seller = kwargs.pop("seller", None) or make_business(
        name=f"سنگ تاریخی {index}",
        owner_phone=f"0921111{index:04d}",
    )
    fields = {
        "product_name": "تراورتن قدیمی",
        "stone_type": "تراورتن",
        "grade": "سوپر",
        "quantity_sqm": Decimal("100.000"),
        "unit_price": Decimal("1500000.00"),
        "total_amount": Decimal("150000000.00"),
        "counterparty_type": "customer",
        "customer_name": "مشتری قدیمی",
        "finalized_at": timezone.now(),
    }
    fields.update(kwargs)
    return Trade.objects.create(seller_business_id=seller.pk, **fields)


def test_every_historical_trade_gets_exactly_one_line(before_backfill):
    trades = [_legacy_trade(before_backfill) for _ in range(3)]

    after = _migrate(AFTER)
    TradeItem = after.get_model("trading", "TradeItem")

    assert TradeItem.objects.count() == 3
    for trade in trades:
        assert TradeItem.objects.filter(trade_id=trade.pk).count() == 1


def test_the_line_carries_the_header_snapshot_verbatim(before_backfill):
    trade = _legacy_trade(before_backfill)

    after = _migrate(AFTER)
    line = after.get_model("trading", "TradeItem").objects.get(trade_id=trade.pk)

    assert line.product_name == "تراورتن قدیمی"
    assert line.stone_type == "تراورتن"
    assert line.grade == "سوپر"
    assert line.quantity == Decimal("100.000")
    assert line.unit_price == Decimal("1500000.00")
    assert line.line_total == Decimal("150000000.00")


def test_the_recorded_total_is_never_recomputed(before_backfill):
    trade = _legacy_trade(
        before_backfill,
        quantity_sqm=Decimal("3.000"),
        unit_price=Decimal("1000000.01"),
        total_amount=Decimal("3000000.00"),
    )

    after = _migrate(AFTER)
    Trade = after.get_model("trading", "Trade")
    TradeItem = after.get_model("trading", "TradeItem")

    assert TradeItem.objects.get(trade_id=trade.pk).line_total == Decimal("3000000.00")
    assert Trade.objects.get(pk=trade.pk).total_amount == Decimal("3000000.00")


def test_the_header_columns_are_left_in_place(before_backfill):
    trade = _legacy_trade(before_backfill)

    after = _migrate(AFTER)
    stored = after.get_model("trading", "Trade").objects.get(pk=trade.pk)

    assert stored.product_name == "تراورتن قدیمی"
    assert stored.quantity_sqm == Decimal("100.000")
    assert stored.unit_price == Decimal("1500000.00")


def test_totals_are_preserved_across_a_mixed_history(before_backfill):
    TradeBefore = before_backfill.get_model("trading", "Trade")
    _legacy_trade(before_backfill, total_amount=Decimal("150000000.00"))
    _legacy_trade(
        before_backfill,
        quantity_sqm=Decimal("70.000"),
        unit_price=Decimal("1200000.00"),
        total_amount=Decimal("84000000.00"),
    )
    _legacy_trade(
        before_backfill,
        quantity_sqm=Decimal("0.500"),
        unit_price=Decimal("999999.99"),
        total_amount=Decimal("500000.00"),
    )
    before = sum(trade.total_amount for trade in TradeBefore.objects.all())

    after = _migrate(AFTER)
    TradeAfter = after.get_model("trading", "Trade")
    TradeItem = after.get_model("trading", "TradeItem")

    after_headers = sum(trade.total_amount for trade in TradeAfter.objects.all())
    after_lines = sum(line.line_total for line in TradeItem.objects.all())
    assert before == after_headers == after_lines


def test_the_backfill_is_reversible_without_losing_anything(before_backfill):
    trade = _legacy_trade(before_backfill)

    _migrate(AFTER)
    before_again = _migrate(BEFORE)
    Trade = before_again.get_model("trading", "Trade")
    TradeItem = before_again.get_model("trading", "TradeItem")

    assert TradeItem.objects.count() == 0
    stored = Trade.objects.get(pk=trade.pk)
    assert stored.total_amount == Decimal("150000000.00")
    assert stored.product_name == "تراورتن قدیمی"


def test_running_the_backfill_twice_does_not_duplicate_lines(before_backfill):
    trade = _legacy_trade(before_backfill)

    _migrate(AFTER)
    before_again = _migrate(BEFORE)
    TradeItem = before_again.get_model("trading", "TradeItem")
    TradeItem.objects.create(
        trade_id=trade.pk,
        product_name="ردیف موجود",
        quantity=Decimal("1.000"),
        unit_price=Decimal("150000000.00"),
        line_total=Decimal("150000000.00"),
    )

    after = _migrate(AFTER)
    rows = after.get_model("trading", "TradeItem").objects.filter(trade_id=trade.pk)

    assert rows.count() == 1
    assert rows.get().product_name == "ردیف موجود"
