"""Bilateral offline agreements are the only colleague-sale UI workflow."""

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
from apps.trading.services import (
    TradingError,
    cancel_trade_proposal,
    confirm_trade_proposal,
    reject_trade_proposal,
    save_trade_proposal,
)


@pytest.fixture
def agreement_market(db):
    ensure_default_tiers()
    seller = make_business(name="فروشنده توافق", owner_phone="09162220001")
    buyer = make_business(name="خریدار توافق", owner_phone="09162220002")
    outsider = make_business(name="غریبه توافق", owner_phone="09162220003")
    item = make_item(
        seller,
        product=make_product(seller, commercial_name="تراورتن توافق"),
        lot_code="AGR-1",
        b2b="1400000",
    )
    return {
        "seller": seller,
        "buyer": buyer,
        "outsider": outsider,
        "seller_m": owner_membership(seller),
        "buyer_m": owner_membership(buyer),
        "outsider_m": owner_membership(outsider),
        "item": item,
    }


def _proposal(market, *, membership=None, lines=None, submit=True):
    return save_trade_proposal(
        seller_business=market["seller"],
        buyer_business=market["buyer"],
        membership=membership or market["seller_m"],
        lines=lines
        or [
            {
                "item": market["item"],
                "quantity": Decimal("12.5"),
                # Deliberately independent from the catalog's B2B price.
                "unit_price": Decimal("1234567"),
            }
        ],
        submission_id=uuid.uuid4(),
        submit=submit,
    )


def test_a_pending_proposal_has_no_financial_effect(agreement_market):
    proposal = _proposal(agreement_market)

    assert proposal.status == TradeProposal.Status.PENDING
    assert proposal.total_amount == Decimal("15432087.50")
    assert Trade.objects.count() == 0
    assert SalesInvoice.objects.count() == 0
    assert LedgerEntry.objects.count() == 0
    assert current_balance(agreement_market["seller"], agreement_market["buyer"]) == 0


def test_the_buyer_may_initiate_against_the_sellers_registered_product(agreement_market):
    proposal = _proposal(agreement_market, membership=agreement_market["buyer_m"])

    assert proposal.initiated_by_business == agreement_market["buyer"]
    assert proposal.items.get().item == agreement_market["item"]
    assert proposal.items.get().unit_price == Decimal("1234567.00")


def test_a_miscellaneous_product_needs_no_inventory_record(agreement_market):
    proposal = _proposal(
        agreement_market,
        lines=[{"product_name": "سنگ متفرقه تلفنی", "quantity": "7", "unit_price": "900000"}],
    )

    line = proposal.items.get()
    assert line.item_id is None
    assert line.product_name == "سنگ متفرقه تلفنی"


def test_only_the_counterparty_may_confirm(agreement_market):
    proposal = _proposal(agreement_market)

    with pytest.raises(TradingError, match="طرف مقابل"):
        confirm_trade_proposal(proposal=proposal, membership=agreement_market["seller_m"])
    with pytest.raises(TradingError, match="طرف این توافق"):
        confirm_trade_proposal(proposal=proposal, membership=agreement_market["outsider_m"])
    assert Trade.objects.count() == 0


def test_confirmation_atomically_creates_trade_two_books_and_issued_invoice(agreement_market):
    proposal = _proposal(agreement_market)
    trade = confirm_trade_proposal(proposal=proposal, membership=agreement_market["buyer_m"])

    proposal.refresh_from_db()
    assert proposal.status == TradeProposal.Status.CONFIRMED
    assert proposal.trade == trade
    assert trade.items.count() == proposal.items.count() == 1
    assert LedgerEntry.objects.filter(related_trade=trade).count() == 2
    assert current_balance(agreement_market["seller"], agreement_market["buyer"]) == trade.total_amount
    assert current_balance(agreement_market["buyer"], agreement_market["seller"]) == -trade.total_amount
    invoice = SalesInvoice.objects.get(trade=trade)
    assert invoice.status == SalesInvoice.Status.ISSUED
    assert invoice.total_amount == trade.total_amount


def test_repeating_confirmation_returns_the_same_financial_event(agreement_market):
    proposal = _proposal(agreement_market)
    first = confirm_trade_proposal(proposal=proposal, membership=agreement_market["buyer_m"])
    second = confirm_trade_proposal(proposal=proposal, membership=agreement_market["buyer_m"])

    assert first == second
    assert Trade.objects.count() == 1
    assert LedgerEntry.objects.count() == 2
    assert SalesInvoice.objects.count() == 1


def test_reject_and_cancel_close_without_financial_records(agreement_market):
    rejected = _proposal(agreement_market)
    reject_trade_proposal(proposal=rejected, membership=agreement_market["buyer_m"])
    cancelled = _proposal(agreement_market)
    cancel_trade_proposal(proposal=cancelled, membership=agreement_market["seller_m"])

    rejected.refresh_from_db()
    cancelled.refresh_from_db()
    assert rejected.status == TradeProposal.Status.REJECTED
    assert cancelled.status == TradeProposal.Status.CANCELLED
    assert Trade.objects.count() == SalesInvoice.objects.count() == LedgerEntry.objects.count() == 0


def test_a_marketplace_product_returns_to_its_market_detail(client, agreement_market):
    client.force_login(agreement_market["buyer_m"].user)
    response = client.get(
        reverse("trading:request_create", kwargs={"item_id": agreement_market["item"].id})
    )

    assert response.status_code == 302
    assert response.url == reverse(
        "marketplace:lot_detail", kwargs={"item_id": agreement_market["item"].id}
    )


def test_both_parties_can_reach_the_trade_invoice_and_colleague_account(client, agreement_market):
    proposal = _proposal(agreement_market)
    trade = confirm_trade_proposal(proposal=proposal, membership=agreement_market["buyer_m"])
    invoice = SalesInvoice.objects.get(trade=trade)

    client.force_login(agreement_market["buyer_m"].user)
    trade_page = client.get(reverse("trading:trade_list"))
    colleague_page = client.get(
        reverse(
            "businesses:colleague_detail",
            kwargs={"business_id": agreement_market["seller"].id},
        )
    )

    assert trade_page.status_code == colleague_page.status_code == 200
    assert "تراورتن توافق" in trade_page.content.decode()
    colleague_body = colleague_page.content.decode()
    assert f"فاکتور {invoice.number}" in colleague_body
    assert "حساب دفتری با این همکار" in colleague_body
    assert "بستانکار" in colleague_body
