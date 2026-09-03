"""A purchase request only moves the ways the product says it may.

Every transition now re-reads the row under a lock and checks the move against
``PurchaseRequest.ALLOWED_TRANSITIONS``. This file pins the sequential half —
the stale browser tab, the back button, the second click a few seconds later.
The simultaneous half needs real connections and lives in
``apps/accounting/tests/test_request_state_concurrency.py``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.core.testing import make_business, make_item, make_product, owner_membership
from apps.pricing.services import ensure_default_tiers
from apps.trading.models import PurchaseRequest, Trade
from apps.trading.services import (
    TradingError,
    cancel_purchase_request,
    create_purchase_request,
    finalize_sale,
    respond_to_purchase_request,
)


@pytest.fixture
def market(db):
    ensure_default_tiers()
    seller = make_business(name="سنگ فروشنده گذار", owner_phone="09181110001")
    buyer = make_business(name="سنگ خریدار گذار", owner_phone="09181110002")
    item = make_item(
        seller,
        product=make_product(seller, commercial_name="تراورتن گذار"),
        lot_code="ST-1",
        b2b="1000000",
    )
    return {
        "seller": seller,
        "buyer": buyer,
        "seller_m": owner_membership(seller),
        "buyer_m": owner_membership(buyer),
        "item": item,
    }


def _sent(market) -> PurchaseRequest:
    return create_purchase_request(
        buyer_business=market["buyer"],
        membership=market["buyer_m"],
        item=market["item"],
        requested_qty_sqm=Decimal("50"),
        proposed_unit_price=Decimal("1000000"),
    )


def _accepted(market) -> PurchaseRequest:
    request = _sent(market)
    return respond_to_purchase_request(request=request, membership=market["seller_m"], accept=True)


def _completed(market) -> PurchaseRequest:
    request = _accepted(market)
    finalize_sale(request=request, membership=market["seller_m"])
    return PurchaseRequest.objects.get(pk=request.pk)


# --- the map is the whole rule ------------------------------------------------


def test_the_three_terminal_states_allow_nothing():
    for status in (
        PurchaseRequest.Status.COMPLETED,
        PurchaseRequest.Status.REJECTED,
        PurchaseRequest.Status.CANCELLED,
    ):
        assert PurchaseRequest.ALLOWED_TRANSITIONS[status] == frozenset()


# --- stale writes -------------------------------------------------------------


def test_a_stale_instance_cannot_cancel_a_completed_request(market):
    """The exact shape of the bug: hold an ACCEPTED instance, finalize through a
    fresh one, then cancel through the stale one."""
    stale = _accepted(market)
    finalize_sale(request=stale, membership=market["seller_m"])

    with pytest.raises(TradingError):
        cancel_purchase_request(request=stale, membership=market["buyer_m"])

    assert PurchaseRequest.objects.get(pk=stale.pk).status == PurchaseRequest.Status.COMPLETED


def test_a_stale_instance_cannot_reject_a_completed_request(market):
    stale = _accepted(market)
    finalize_sale(request=stale, membership=market["seller_m"])

    with pytest.raises(TradingError):
        respond_to_purchase_request(request=stale, membership=market["seller_m"], accept=False)

    assert PurchaseRequest.objects.get(pk=stale.pk).status == PurchaseRequest.Status.COMPLETED


def test_a_stale_instance_cannot_re_answer_an_answered_request(market):
    stale = _sent(market)
    respond_to_purchase_request(request=stale, membership=market["seller_m"], accept=True)

    with pytest.raises(TradingError):
        respond_to_purchase_request(request=stale, membership=market["seller_m"], accept=False)

    assert PurchaseRequest.objects.get(pk=stale.pk).status == PurchaseRequest.Status.ACCEPTED


def test_a_cancelled_request_cannot_be_accepted(market):
    request = _sent(market)
    cancel_purchase_request(request=request, membership=market["buyer_m"])

    with pytest.raises(TradingError):
        respond_to_purchase_request(request=request, membership=market["seller_m"], accept=True)

    assert PurchaseRequest.objects.get(pk=request.pk).status == PurchaseRequest.Status.CANCELLED


def test_a_cancelled_request_cannot_be_finalized(market):
    request = _accepted(market)
    cancel_purchase_request(request=request, membership=market["buyer_m"])

    with pytest.raises(TradingError):
        finalize_sale(request=request, membership=market["seller_m"])

    assert Trade.objects.count() == 0


def test_a_rejected_request_cannot_be_cancelled(market):
    request = _sent(market)
    respond_to_purchase_request(request=request, membership=market["seller_m"], accept=False)

    with pytest.raises(TradingError):
        cancel_purchase_request(request=request, membership=market["buyer_m"])

    assert PurchaseRequest.objects.get(pk=request.pk).status == PurchaseRequest.Status.REJECTED


def test_a_completed_request_cannot_be_finalized_again(market):
    request = _accepted(market)
    finalize_sale(request=request, membership=market["seller_m"])

    with pytest.raises(TradingError):
        finalize_sale(request=request, membership=market["seller_m"])

    assert Trade.objects.count() == 1


# --- the transitions that are still allowed -----------------------------------


def test_cancelling_before_finalization_is_still_allowed(market):
    """The deal falling through after agreement is ordinary trading, and the
    tightened rules must not take it away."""
    request = _accepted(market)

    cancelled = cancel_purchase_request(request=request, membership=market["buyer_m"])

    assert cancelled.status == PurchaseRequest.Status.CANCELLED
    assert Trade.objects.count() == 0


def test_a_sent_request_can_still_be_cancelled_accepted_or_rejected(market):
    cancelled = cancel_purchase_request(request=_sent(market), membership=market["buyer_m"])
    assert cancelled.status == PurchaseRequest.Status.CANCELLED

    accepted = respond_to_purchase_request(
        request=_sent(market), membership=market["seller_m"], accept=True
    )
    assert accepted.status == PurchaseRequest.Status.ACCEPTED

    rejected = respond_to_purchase_request(
        request=_sent(market), membership=market["seller_m"], accept=False
    )
    assert rejected.status == PurchaseRequest.Status.REJECTED


# --- a trade and a refusal cannot coexist -------------------------------------


def test_a_request_owning_a_trade_can_never_end_cancelled_or_rejected(market):
    """The invariant this whole change exists for."""
    completed = _completed(market)
    assert Trade.objects.filter(purchase_request=completed).exists()

    for action in (
        lambda: cancel_purchase_request(request=completed, membership=market["buyer_m"]),
        lambda: respond_to_purchase_request(
            request=completed, membership=market["seller_m"], accept=False
        ),
    ):
        with pytest.raises(TradingError):
            action()

    assert PurchaseRequest.objects.get(pk=completed.pk).status == PurchaseRequest.Status.COMPLETED
