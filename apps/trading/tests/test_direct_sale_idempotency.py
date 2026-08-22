"""One submission is one sale, however many times it arrives.

A direct sale used to have no idempotency at all. Finalizing a purchase request
was protected by the OneToOneField to the request plus its row lock, but a phone
sale has no request, so a double-click, a refresh or a reverse proxy retrying a
timed-out POST produced two Trades describing one real-world sale — and because
they were genuinely distinct rows, every per-trade constraint was satisfied while
the colleague's balance moved twice.

The race itself lives in ``apps/accounting/tests/test_financial_concurrency.py``,
because only PostgreSQL can demonstrate it. This file pins the sequential half:
the retry a user actually performs.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.accounting.models import LedgerEntry
from apps.accounting.selectors import current_balance
from apps.core.testing import make_business, make_item, make_product, owner_membership
from apps.invoicing.models import SalesInvoice
from apps.pricing.services import ensure_default_tiers
from apps.trading.models import Trade, TradeProposal
from apps.trading.services import record_direct_sale


@pytest.fixture
def market(db):
    ensure_default_tiers()
    seller = make_business(name="سنگ فروشنده تکرار", owner_phone="09171110001")
    buyer = make_business(name="سنگ خریدار تکرار", owner_phone="09171110002")
    item = make_item(
        seller,
        product=make_product(seller, commercial_name="تراورتن تکرار"),
        lot_code="IDEM-1",
        b2b="1000000",
    )
    return {
        "seller": seller,
        "buyer": buyer,
        "seller_m": owner_membership(seller),
        "item": item,
    }


def _sale(market, submission_id, **kwargs):
    params = {
        "seller_business": market["seller"],
        "membership": market["seller_m"],
        "item": market["item"],
        "quantity_sqm": Decimal("10"),
        "unit_price": Decimal("1000000"),
        "buyer_business": market["buyer"],
        "submission_id": submission_id,
    }
    params.update(kwargs)
    return record_direct_sale(**params)


def test_resubmitting_one_sale_returns_the_original_trade(market):
    token = uuid.uuid4()

    first = _sale(market, token)
    second = _sale(market, token)

    assert first.pk == second.pk
    assert Trade.objects.filter(seller_business=market["seller"]).count() == 1


def test_resubmitting_one_sale_moves_each_book_once(market):
    token = uuid.uuid4()

    _sale(market, token)
    _sale(market, token)

    trade = Trade.objects.get()
    assert LedgerEntry.objects.filter(related_trade=trade, entry_type=LedgerEntry.Type.SALE).count() == 1
    assert LedgerEntry.objects.filter(related_trade=trade, entry_type=LedgerEntry.Type.PURCHASE).count() == 1
    assert current_balance(market["seller"], market["buyer"]) == Decimal("10000000.00")
    assert current_balance(market["buyer"], market["seller"]) == Decimal("-10000000.00")


def test_resubmitting_one_sale_issues_one_invoice(market):
    token = uuid.uuid4()

    _sale(market, token)
    _sale(market, token)

    assert SalesInvoice.objects.count() == 1


def test_a_retry_issues_the_invoice_the_first_attempt_could_not(market):
    """Invoicing is best-effort, so a lapsed entitlement or a transient failure
    can leave a finalized sale with no document. A retry is the natural moment to
    heal that, and returning early on the idempotency check used to skip it —
    leaving the sale permanently undocumented unless somebody found the manual
    button."""
    token = uuid.uuid4()
    trade = _sale(market, token)
    SalesInvoice.objects.filter(trade=trade).delete()

    again = _sale(market, token)

    assert again.pk == trade.pk
    assert SalesInvoice.objects.filter(trade=trade).count() == 1


def test_healing_the_invoice_does_not_move_the_books_again(market):
    """The other half: the retry must not re-post the ledger while it is fixing
    the document."""
    token = uuid.uuid4()
    trade = _sale(market, token)
    SalesInvoice.objects.filter(trade=trade).delete()

    _sale(market, token)

    assert LedgerEntry.objects.filter(related_trade=trade).count() == 2
    assert current_balance(market["seller"], market["buyer"]) == Decimal("10000000.00")


def test_a_different_submission_is_a_different_sale(market):
    """The token must not deduplicate two genuinely separate sales of the same
    product to the same colleague on the same day, which is ordinary trading."""
    _sale(market, uuid.uuid4())
    _sale(market, uuid.uuid4())

    assert Trade.objects.count() == 2
    assert current_balance(market["seller"], market["buyer"]) == Decimal("20000000.00")


def test_the_same_token_from_two_sellers_does_not_collide(market):
    """Uniqueness is scoped by seller, so one business cannot block another's
    sale by happening to mint the same value."""
    other = make_business(name="سنگ فروشنده دیگر", owner_phone="09171110003")
    other_item = make_item(other, lot_code="IDEM-2", b2b="1000000")
    token = uuid.uuid4()

    _sale(market, token)
    record_direct_sale(
        seller_business=other,
        membership=owner_membership(other),
        item=other_item,
        quantity_sqm=Decimal("5"),
        unit_price=Decimal("1000000"),
        buyer_business=market["buyer"],
        submission_id=token,
    )

    assert Trade.objects.count() == 2


def test_a_sale_recorded_without_a_token_is_still_allowed(market):
    """``submission_id`` is optional: nothing outside the form has to mint one,
    and historical rows have none."""
    _sale(market, None)
    _sale(market, None)

    assert Trade.objects.count() == 2


# --- through the view ---------------------------------------------------------


def test_the_agreement_form_carries_a_token_and_a_double_post_records_one_proposal(client, market):
    client.force_login(market["seller_m"].user)
    url = reverse("trading:proposal_create")

    page = client.get(url)
    token = page.context["form"]["submission_id"].value()
    assert token, "the blank form must mint a submission token"

    payload = {
        "submission_id": str(token),
        "direction": "sell",
        "counterparty": str(market["buyer"].id),
        "lines-TOTAL_FORMS": "1",
        "lines-INITIAL_FORMS": "0",
        "lines-0-item": str(market["item"].id),
        "lines-0-quantity": "10",
        "lines-0-unit_price": "1000000",
    }
    client.post(url, payload)
    client.post(url, payload)

    assert TradeProposal.objects.count() == 1
    assert Trade.objects.count() == 0
    assert SalesInvoice.objects.count() == 0
    assert current_balance(market["seller"], market["buyer"]) == Decimal("0.00")
