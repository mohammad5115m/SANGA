"""Financial invariants under real concurrency.

Everything here is marked ``concurrency`` and runs only on the PostgreSQL lane
(``scripts/run_pg_tests.sh``). SQLite serializes writers behind one database
lock and ignores ``select_for_update``, so it cannot fail these tests even when
the locking is wrong — which is precisely how a race survives a green suite.

Each test races two real connections through the same service and asserts the
invariant the database is supposed to hold, not merely that no exception
escaped.
"""

from __future__ import annotations

import threading
import uuid
from decimal import Decimal

import pytest
from django.db import connection

from apps.accounting.models import LedgerEntry
from apps.accounting.selectors import current_balance
from apps.accounting.services import LedgerDuplicateError, post_trade_entries
from apps.core.testing import make_business, make_item, make_product, owner_membership
from apps.invoicing.models import SalesInvoice
from apps.invoicing.services import create_invoice_for_trade
from apps.pricing.services import ensure_default_tiers
from apps.trading.models import PurchaseRequest, Trade
from apps.trading.services import (
    TradingError,
    confirm_trade_proposal,
    create_purchase_request,
    finalize_sale,
    record_direct_sale,
    respond_to_purchase_request,
    save_trade_proposal,
)

pytestmark = [pytest.mark.concurrency, pytest.mark.django_db(transaction=True)]


def _race(target, count: int = 2) -> tuple[list, list]:
    """Run ``target`` on ``count`` threads released at the same instant.

    A barrier rather than a bare thread start: without it the first thread
    routinely finishes before the second begins, and the test passes against
    code that has no concurrency control at all.
    """
    barrier = threading.Barrier(count, timeout=15)
    results: list = []
    errors: list = []
    lock = threading.Lock()

    def runner() -> None:
        try:
            barrier.wait()
            outcome = target()
        except Exception as exc:  # noqa: BLE001 - the test inspects what escaped
            with lock:
                errors.append(exc)
        else:
            with lock:
                results.append(outcome)
        finally:
            # Each thread owns its own connection; leaking them exhausts the
            # test database's connection slots across a whole run.
            connection.close()

    threads = [threading.Thread(target=runner) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return results, errors


def _world():
    ensure_default_tiers()
    seller = make_business(name="سنگ فروشنده همزمان", owner_phone="09391110001")
    buyer = make_business(name="سنگ خریدار همزمان", owner_phone="09391110002")
    item = make_item(
        seller,
        product=make_product(seller, commercial_name="تراورتن همزمان"),
        lot_code="CC-1",
        b2b="1000000",
    )
    return {
        "seller": seller,
        "buyer": buyer,
        "item": item,
        "seller_m": owner_membership(seller),
        "buyer_m": owner_membership(buyer),
    }


def _accepted_request(world) -> PurchaseRequest:
    request = create_purchase_request(
        buyer_business=world["buyer"],
        membership=world["buyer_m"],
        item=world["item"],
        requested_qty_sqm=Decimal("50"),
        proposed_unit_price=Decimal("1000000"),
    )
    respond_to_purchase_request(request=request, membership=world["seller_m"], accept=True)
    return request


def test_two_threads_invoicing_one_trade_produce_one_document():
    """AUD-001. The old code checked for an existing invoice *before* taking the
    lock, so both callers could see none and both create one."""
    world = _world()
    trade = record_direct_sale(
        seller_business=world["seller"],
        membership=world["seller_m"],
        item=world["item"],
        quantity_sqm=Decimal("10"),
        unit_price=Decimal("1000000"),
        buyer_business=world["buyer"],
    )
    # Finalization already issued one; drop it so both threads start from the
    # state a genuine race begins in.
    SalesInvoice.objects.filter(trade=trade).delete()

    results, errors = _race(
        lambda: create_invoice_for_trade(trade=trade, membership=world["seller_m"])
    )

    assert errors == []
    assert SalesInvoice.objects.filter(trade=trade).count() == 1
    assert len({invoice.pk for invoice in results}) == 1, "both callers must resolve to the same document"


def test_two_threads_posting_one_trade_move_each_book_once():
    """AUD-003 under contention: two sides, each posted exactly once."""
    world = _world()
    trade = record_direct_sale(
        seller_business=world["seller"],
        membership=world["seller_m"],
        item=world["item"],
        quantity_sqm=Decimal("10"),
        unit_price=Decimal("1000000"),
        buyer_business=world["buyer"],
    )
    # record_direct_sale already posted both books; racing again must add none.
    results, errors = _race(
        lambda: post_trade_entries(trade=trade, membership=world["seller_m"])
    )

    assert results == []
    assert all(isinstance(error, LedgerDuplicateError) for error in errors), errors
    assert LedgerEntry.objects.filter(related_trade=trade, entry_type=LedgerEntry.Type.SALE).count() == 1
    assert LedgerEntry.objects.filter(related_trade=trade, entry_type=LedgerEntry.Type.PURCHASE).count() == 1
    assert current_balance(world["seller"], world["buyer"]) == Decimal("10000000.00")
    assert current_balance(world["buyer"], world["seller"]) == Decimal("-10000000.00")


def test_two_threads_finalizing_one_request_produce_one_sale():
    """The whole chain under a double-submitted POST: one trade, one entry per
    book, one invoice."""
    world = _world()
    request = _accepted_request(world)

    results, errors = _race(
        lambda: finalize_sale(request=request, membership=world["seller_m"])
    )

    assert len(results) == 1, f"exactly one finalization must win; errors={errors}"
    assert all(isinstance(error, TradingError) for error in errors), errors

    trade = results[0]
    assert LedgerEntry.objects.filter(related_trade=trade).count() == 2
    assert SalesInvoice.objects.filter(trade=trade).count() == 1
    assert current_balance(world["seller"], world["buyer"]) == Decimal("50000000.00")


def test_two_threads_confirming_one_bilateral_agreement_produce_one_financial_event():
    world = _world()
    proposal = save_trade_proposal(
        seller_business=world["seller"],
        buyer_business=world["buyer"],
        membership=world["seller_m"],
        lines=[{"item": world["item"], "quantity": "10", "unit_price": "1000000"}],
    )

    results, errors = _race(
        lambda: confirm_trade_proposal(proposal=proposal, membership=world["buyer_m"])
    )

    assert errors == []
    assert len(results) == 2
    assert len({trade.pk for trade in results}) == 1
    trade = results[0]
    assert Trade.objects.count() == 1
    assert LedgerEntry.objects.filter(related_trade=trade).count() == 2
    assert SalesInvoice.objects.filter(trade=trade).count() == 1
    assert current_balance(world["seller"], world["buyer"]) == Decimal("10000000.00")


def test_two_threads_submitting_one_direct_sale_record_one_sale():
    """The double-submitted phone sale.

    A direct sale has no PurchaseRequest, so it inherited neither the
    OneToOneField nor the row lock that make finalization idempotent. Two
    connections arriving with the same submission token used to create two
    Trades — genuinely distinct rows, so ``uniq_trade_entry_per_trade`` was
    satisfied by both and the colleague was billed twice for one sale.
    """
    world = _world()
    token = uuid.uuid4()

    results, errors = _race(
        lambda: record_direct_sale(
            seller_business=world["seller"],
            membership=world["seller_m"],
            item=world["item"],
            quantity_sqm=Decimal("10"),
            unit_price=Decimal("1000000"),
            buyer_business=world["buyer"],
            submission_id=token,
        )
    )

    assert errors == [], f"no caller may see a raw IntegrityError; got {errors}"
    assert len(results) == 2, "both callers must be answered"
    assert len({trade.pk for trade in results}) == 1, "both callers must resolve to the same sale"

    assert Trade.objects.filter(seller_business=world["seller"]).count() == 1
    trade = results[0]
    assert LedgerEntry.objects.filter(related_trade=trade, entry_type=LedgerEntry.Type.SALE).count() == 1
    assert LedgerEntry.objects.filter(related_trade=trade, entry_type=LedgerEntry.Type.PURCHASE).count() == 1
    assert SalesInvoice.objects.filter(trade=trade).count() == 1

    assert current_balance(world["seller"], world["buyer"]) == Decimal("10000000.00")
    assert current_balance(world["buyer"], world["seller"]) == Decimal("-10000000.00")


def test_four_threads_submitting_one_direct_sale_still_record_one_sale():
    """Two threads can serialize by luck; four make that much less likely."""
    world = _world()
    token = uuid.uuid4()

    results, errors = _race(
        lambda: record_direct_sale(
            seller_business=world["seller"],
            membership=world["seller_m"],
            item=world["item"],
            quantity_sqm=Decimal("10"),
            unit_price=Decimal("1000000"),
            buyer_business=world["buyer"],
            submission_id=token,
        ),
        count=4,
    )

    assert errors == [], errors
    assert len({trade.pk for trade in results}) == 1
    assert Trade.objects.filter(seller_business=world["seller"]).count() == 1
    assert current_balance(world["seller"], world["buyer"]) == Decimal("10000000.00")


def test_concurrent_invoice_numbering_never_collides():
    """Numbers are allocated under the seller lock, so a burst cannot duplicate
    one — the unique constraint would reject it if it did."""
    world = _world()
    trades = [
        record_direct_sale(
            seller_business=world["seller"],
            membership=world["seller_m"],
            item=world["item"],
            quantity_sqm=Decimal("1"),
            unit_price=Decimal("1000000"),
            customer_name=f"مشتری {index}",
        )
        for index in range(4)
    ]
    for trade in trades:
        SalesInvoice.objects.filter(trade=trade).delete()

    queue = list(trades)
    lock = threading.Lock()

    def claim():
        with lock:
            trade = queue.pop()
        return create_invoice_for_trade(trade=trade, membership=world["seller_m"])

    results, errors = _race(claim, count=4)

    assert errors == []
    numbers = [invoice.number for invoice in results]
    assert len(set(numbers)) == len(numbers), f"invoice numbers collided: {numbers}"
