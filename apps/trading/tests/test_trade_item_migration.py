"""Historical trades must arrive on the other side of the refactor unchanged.

Before ``TradeItem`` a Trade *was* its single line. Reads have moved to the
lines, so every existing trade needs one — and the one thing that must not
happen while giving it one is a change to what somebody was invoiced.

These tests drive the real migration rather than calling the backfill function,
because asserting on the function proves only that the function does what it
says. ``0004`` is data-only, so the schema either side of it is identical and the
ordinary models describe both.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from apps.core.testing import make_business
from apps.trading.models import Trade, TradeItem

BEFORE = "0003_trade_items"
AFTER = "0004_backfill_trade_items"

pytestmark = pytest.mark.django_db(transaction=True)


def _migrate(migration: str) -> None:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([("trading", migration)])
    executor.loader.build_graph()


@pytest.fixture
def before_backfill():
    """Reverse the backfill, leaving trades with no lines — the pre-refactor shape."""
    _migrate(BEFORE)
    yield
    # Leave the database at head, or every later test in the process runs against
    # a half-migrated schema.
    _migrate(AFTER)


def _legacy_trade(**kwargs) -> Trade:
    """A trade in the shape the old code wrote: header columns, no lines."""
    seller = kwargs.pop("seller", None) or make_business(
        name=f"سنگ تاریخی {Trade.objects.count()}",
        owner_phone=f"0921111{Trade.objects.count():04d}",
    )
    fields = {
        "product_name": "تراورتن قدیمی",
        "stone_type": "تراورتن",
        "grade": "سوپر",
        "quantity_sqm": Decimal("100.000"),
        "unit_price": Decimal("1500000.00"),
        "total_amount": Decimal("150000000.00"),
        "counterparty_type": Trade.Counterparty.CUSTOMER,
        "customer_name": "مشتری قدیمی",
        "finalized_at": timezone.now(),
    }
    fields.update(kwargs)
    return Trade.objects.create(seller_business=seller, **fields)


def test_every_historical_trade_gets_exactly_one_line(before_backfill):
    trades = [_legacy_trade() for _ in range(3)]

    _migrate(AFTER)

    assert TradeItem.objects.count() == 3
    for trade in trades:
        assert trade.items.count() == 1


def test_the_line_carries_the_header_snapshot_verbatim(before_backfill):
    trade = _legacy_trade()

    _migrate(AFTER)

    line = trade.items.get()
    assert line.product_name == "تراورتن قدیمی"
    assert line.stone_type == "تراورتن"
    assert line.grade == "سوپر"
    assert line.quantity == Decimal("100.000")
    assert line.unit_price == Decimal("1500000.00")
    assert line.line_total == Decimal("150000000.00")


def test_the_recorded_total_is_never_recomputed(before_backfill):
    """A historical total is a commercial fact. If quantity × unit price
    disagrees with it — a rounding difference, a hand-edited row — the recorded
    total wins, because a document that has already been sent to a customer does
    not get quietly corrected years later.
    """
    trade = _legacy_trade(
        quantity_sqm=Decimal("3.000"),
        unit_price=Decimal("1000000.01"),
        total_amount=Decimal("3000000.00"),
    )

    _migrate(AFTER)

    assert trade.items.get().line_total == Decimal("3000000.00")
    assert Trade.objects.get(pk=trade.pk).total_amount == Decimal("3000000.00")


def test_the_header_columns_are_left_in_place(before_backfill):
    """They are still what a one-line sale writes, so nothing that already reads
    them breaks. Dropping them is a later, separate change."""
    trade = _legacy_trade()

    _migrate(AFTER)

    stored = Trade.objects.get(pk=trade.pk)
    assert stored.product_name == "تراورتن قدیمی"
    assert stored.quantity_sqm == Decimal("100.000")
    assert stored.unit_price == Decimal("1500000.00")


def test_totals_are_preserved_across_a_mixed_history(before_backfill):
    """The invariant the migration refuses to complete without."""
    _legacy_trade(total_amount=Decimal("150000000.00"))
    _legacy_trade(
        quantity_sqm=Decimal("70.000"),
        unit_price=Decimal("1200000.00"),
        total_amount=Decimal("84000000.00"),
    )
    _legacy_trade(
        quantity_sqm=Decimal("0.500"),
        unit_price=Decimal("999999.99"),
        total_amount=Decimal("500000.00"),
    )
    before = sum(trade.total_amount for trade in Trade.objects.all())

    _migrate(AFTER)

    after_headers = sum(trade.total_amount for trade in Trade.objects.all())
    after_lines = sum(line.line_total for line in TradeItem.objects.all())
    assert before == after_headers == after_lines


def test_the_backfill_is_reversible_without_losing_anything(before_backfill):
    trade = _legacy_trade()

    _migrate(AFTER)
    _migrate(BEFORE)

    assert TradeItem.objects.count() == 0
    stored = Trade.objects.get(pk=trade.pk)
    assert stored.total_amount == Decimal("150000000.00")
    assert stored.product_name == "تراورتن قدیمی"


def test_running_the_backfill_twice_does_not_duplicate_lines(before_backfill):
    """A rerun against a partially migrated database must be a no-op, not a
    second line on every trade."""
    trade = _legacy_trade()

    _migrate(AFTER)
    _migrate(BEFORE)
    TradeItem.objects.create(
        trade=trade,
        product_name="ردیف موجود",
        quantity=Decimal("1.000"),
        unit_price=Decimal("150000000.00"),
        line_total=Decimal("150000000.00"),
    )
    _migrate(AFTER)

    assert trade.items.count() == 1
    assert trade.items.get().product_name == "ردیف موجود"
