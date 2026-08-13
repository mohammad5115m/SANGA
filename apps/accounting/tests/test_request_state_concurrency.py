"""Two people acting on one purchase request at the same instant.

Runs only on the PostgreSQL lane. ``finalize_sale`` already re-read the request
under ``select_for_update``; ``cancel_purchase_request`` and
``respond_to_purchase_request`` validated the caller's in-memory instance and
wrote straight over whatever the database now held. Under READ COMMITTED that
lets two transactions each read ``ACCEPTED`` and each write a different terminal
status, and the worst available outcome is the one that actually happened: a
CANCELLED request owning a Trade, a ledger pair and an invoice.

SQLite cannot demonstrate any of it — it serializes writers behind one database
lock and ignores ``select_for_update``, so the second caller always arrives after
the first has committed.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest
from django.db import connection

from apps.accounting.models import LedgerEntry
from apps.core.testing import make_business, make_item, make_product, owner_membership
from apps.invoicing.models import SalesInvoice
from apps.pricing.services import ensure_default_tiers
from apps.trading.models import PurchaseRequest, Trade
from apps.trading.services import (
    cancel_purchase_request,
    create_purchase_request,
    finalize_sale,
    respond_to_purchase_request,
)

pytestmark = [pytest.mark.concurrency, pytest.mark.django_db(transaction=True)]


def _race(*targets) -> tuple[list, list]:
    """Run each target on its own thread, all released at the same instant.

    Takes different callables rather than one repeated callable, because the
    interesting races here are between *different* actions.
    """
    barrier = threading.Barrier(len(targets), timeout=15)
    results: list = []
    errors: list = []
    lock = threading.Lock()

    def runner(target) -> None:
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
            connection.close()

    threads = [threading.Thread(target=runner, args=(target,)) for target in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return results, errors


def _world():
    ensure_default_tiers()
    seller = make_business(name="سنگ فروشنده وضعیت", owner_phone="09391120001")
    buyer = make_business(name="سنگ خریدار وضعیت", owner_phone="09391120002")
    item = make_item(
        seller,
        product=make_product(seller, commercial_name="تراورتن وضعیت"),
        lot_code="SC-1",
        b2b="1000000",
    )
    return {
        "seller": seller,
        "buyer": buyer,
        "item": item,
        "seller_m": owner_membership(seller),
        "buyer_m": owner_membership(buyer),
    }


def _sent(world) -> PurchaseRequest:
    return create_purchase_request(
        buyer_business=world["buyer"],
        membership=world["buyer_m"],
        item=world["item"],
        requested_qty_sqm=Decimal("50"),
        proposed_unit_price=Decimal("1000000"),
    )


def _accepted(world) -> PurchaseRequest:
    request = _sent(world)
    respond_to_purchase_request(request=request, membership=world["seller_m"], accept=True)
    return request


def _assert_settled(request: PurchaseRequest) -> str:
    """The status must be one of the terminal ones, and it must match reality."""
    status = PurchaseRequest.objects.get(pk=request.pk).status
    has_trade = Trade.objects.filter(purchase_request_id=request.pk).exists()
    if has_trade:
        assert status == PurchaseRequest.Status.COMPLETED, (
            f"a request owning a Trade settled as {status}"
        )
    return status


# --- A. accept vs reject ------------------------------------------------------


def test_accepting_and_rejecting_at_once_leaves_exactly_one_answer():
    world = _world()
    request = _sent(world)

    results, _errors = _race(
        lambda: respond_to_purchase_request(
            request=request, membership=world["seller_m"], accept=True
        ),
        lambda: respond_to_purchase_request(
            request=request, membership=world["seller_m"], accept=False
        ),
    )

    assert len(results) == 1, "one of the two answers must be refused"
    final = PurchaseRequest.objects.get(pk=request.pk)
    assert final.status in {PurchaseRequest.Status.ACCEPTED, PurchaseRequest.Status.REJECTED}
    assert final.status == results[0].status, "the winner's decision must be the stored one"


# --- B. cancel vs accept ------------------------------------------------------


def test_cancelling_and_accepting_at_once_leaves_exactly_one_outcome():
    world = _world()
    request = _sent(world)

    results, _errors = _race(
        lambda: cancel_purchase_request(request=request, membership=world["buyer_m"]),
        lambda: respond_to_purchase_request(
            request=request, membership=world["seller_m"], accept=True
        ),
    )

    assert len(results) == 1, "the buyer's cancel and the seller's accept must not both land"
    final = PurchaseRequest.objects.get(pk=request.pk)
    assert final.status == results[0].status


# --- C. cancel vs finalize ----------------------------------------------------


def test_cancelling_while_finalizing_never_leaves_a_cancelled_sale():
    """The dangerous one.

    If finalization wins, a Trade, two ledger entries and an invoice exist and
    the request must read COMPLETED. If the cancel wins, none of them may exist.
    What must never happen is a CANCELLED request that owns a sale.
    """
    world = _world()
    request = _accepted(world)

    results, _errors = _race(
        lambda: finalize_sale(request=request, membership=world["seller_m"]),
        lambda: cancel_purchase_request(request=request, membership=world["buyer_m"]),
    )

    assert len(results) == 1, "finalizing and cancelling must not both succeed"
    status = _assert_settled(request)

    if status == PurchaseRequest.Status.COMPLETED:
        trade = Trade.objects.get(purchase_request_id=request.pk)
        assert LedgerEntry.objects.filter(related_trade=trade).count() == 2
        assert SalesInvoice.objects.filter(trade=trade).count() == 1
    else:
        assert status == PurchaseRequest.Status.CANCELLED
        assert Trade.objects.count() == 0
        assert LedgerEntry.objects.count() == 0
        assert SalesInvoice.objects.count() == 0


# --- D. finalize vs finalize --------------------------------------------------


def test_finalizing_twice_at_once_records_one_sale():
    world = _world()
    request = _accepted(world)

    results, _errors = _race(
        lambda: finalize_sale(request=request, membership=world["seller_m"]),
        lambda: finalize_sale(request=request, membership=world["seller_m"]),
    )

    assert len(results) == 1
    assert Trade.objects.filter(purchase_request_id=request.pk).count() == 1
    assert _assert_settled(request) == PurchaseRequest.Status.COMPLETED
    assert LedgerEntry.objects.filter(related_trade=results[0]).count() == 2
    assert SalesInvoice.objects.filter(trade=results[0]).count() == 1


# --- E. the stale browser POST ------------------------------------------------


def test_a_stale_post_arriving_after_completion_changes_nothing():
    """A tab opened while the request was ACCEPTED, submitted after somebody
    else finalized. The instance it holds is not the row that exists."""
    world = _world()
    stale = _accepted(world)
    finalize_sale(request=stale, membership=world["seller_m"])

    results, errors = _race(
        lambda: cancel_purchase_request(request=stale, membership=world["buyer_m"]),
        lambda: respond_to_purchase_request(
            request=stale, membership=world["seller_m"], accept=False
        ),
    )

    assert results == [], "no stale write may land on a completed request"
    assert len(errors) == 2
    assert _assert_settled(stale) == PurchaseRequest.Status.COMPLETED
    assert Trade.objects.count() == 1
