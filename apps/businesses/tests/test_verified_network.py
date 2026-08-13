"""Only approved businesses participate in the shared network.

Eligibility used to exclude only REJECTED and SUSPENDED, so UNVERIFIED and
PENDING businesses appeared in the colleague directory, the marketplace and
public search. In a platform with no self-service signup that is backwards: a
Business exists because an operator provisioned it, so approval is a decision
somebody has already made and the field should record it.

The matrix below exists because the old drift was only ever visible when the
surfaces were compared side by side — and because the one thing this change must
*not* do is take away access to history. A colleague who is suspended tomorrow
still owes money today, and both parties must be able to open the statement and
the invoices that prove it.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.accounting.selectors import accounting_counterparty, current_balance
from apps.businesses.directory import colleague_businesses, get_colleague
from apps.businesses.models import Business
from apps.core.testing import make_business, make_item, make_product, owner_membership
from apps.inventory.policy import eligible_items
from apps.invoicing.selectors import invoices_for
from apps.marketplace.selectors import marketplace_lots_for
from apps.pricing.services import ensure_default_tiers
from apps.trading.services import TradingError, create_purchase_request, record_direct_sale

APPROVED = Business.VerificationStatus.VERIFIED
NOT_APPROVED = [
    Business.VerificationStatus.UNVERIFIED,
    Business.VerificationStatus.PENDING,
    Business.VerificationStatus.REJECTED,
    Business.VerificationStatus.SUSPENDED,
]
EVERY_STATE = [APPROVED, *NOT_APPROVED]


@pytest.fixture
def network(db):
    ensure_default_tiers()
    seller = make_business(name="سنگ فروشنده شبکه", owner_phone="09201110001")
    buyer = make_business(name="سنگ خریدار شبکه", owner_phone="09201110002")
    item = make_item(
        seller,
        product=make_product(seller, commercial_name="تراورتن شبکه"),
        lot_code="NV-1",
        b2b="1000000",
        b2c="1500000",
    )
    return {
        "seller": seller,
        "buyer": buyer,
        "item": item,
        "seller_m": owner_membership(seller),
        "buyer_m": owner_membership(buyer),
    }


def _verify(business: Business, state: str) -> Business:
    business.verification_status = state
    business.save(update_fields=["verification_status"])
    return business


# --- discovery surfaces -------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("state", EVERY_STATE)
def test_the_colleague_directory_lists_only_approved_businesses(network, state):
    _verify(network["seller"], state)
    listed = colleague_businesses(network["buyer"])
    assert (network["seller"] in listed) is (state == APPROVED)


@pytest.mark.django_db
@pytest.mark.parametrize("state", EVERY_STATE)
def test_the_marketplace_shows_only_approved_sellers(network, state):
    _verify(network["seller"], state)
    lots = marketplace_lots_for(network["buyer"])
    assert (network["item"] in lots) is (state == APPROVED)


@pytest.mark.django_db
@pytest.mark.parametrize("state", EVERY_STATE)
def test_public_search_shows_only_approved_sellers(network, state):
    _verify(network["seller"], state)
    public = eligible_items(audience="public")
    assert (network["item"] in public) is (state == APPROVED)


@pytest.mark.django_db
@pytest.mark.parametrize("state", EVERY_STATE)
def test_an_unapproved_viewer_sees_nothing_either(network, state):
    """Both directions. An unapproved buyer must not browse the network, or the
    policy only protects sellers."""
    _verify(network["buyer"], state)
    lots = marketplace_lots_for(network["buyer"])
    assert (network["item"] in lots) is (state == APPROVED)


@pytest.mark.django_db
@pytest.mark.parametrize("state", NOT_APPROVED)
def test_an_unapproved_seller_cannot_be_sent_a_purchase_request(network, state):
    _verify(network["seller"], state)
    with pytest.raises(TradingError):
        create_purchase_request(
            buyer_business=network["buyer"],
            membership=network["buyer_m"],
            item=network["item"],
            requested_qty_sqm=Decimal("10"),
            proposed_unit_price=Decimal("1000000"),
        )


@pytest.mark.django_db
@pytest.mark.parametrize("state", NOT_APPROVED)
def test_an_unapproved_buyer_cannot_send_a_purchase_request(network, state):
    _verify(network["buyer"], state)
    with pytest.raises(TradingError):
        create_purchase_request(
            buyer_business=network["buyer"],
            membership=network["buyer_m"],
            item=network["item"],
            requested_qty_sqm=Decimal("10"),
            proposed_unit_price=Decimal("1000000"),
        )


@pytest.mark.django_db
def test_an_approved_seller_is_reachable_end_to_end(network):
    request = create_purchase_request(
        buyer_business=network["buyer"],
        membership=network["buyer_m"],
        item=network["item"],
        requested_qty_sqm=Decimal("10"),
        proposed_unit_price=Decimal("1000000"),
    )
    assert request.pk is not None


# --- history is not subject to current eligibility ----------------------------


def _trade_between(network):
    return record_direct_sale(
        seller_business=network["seller"],
        membership=network["seller_m"],
        buyer_business=network["buyer"],
        item=network["item"],
        quantity_sqm=Decimal("10"),
        unit_price=Decimal("1000000"),
    )


@pytest.mark.django_db
@pytest.mark.parametrize("state", NOT_APPROVED)
def test_a_suspended_colleagues_balance_stays_readable(network, state):
    """A colleague who loses network access tomorrow still owes money today."""
    _trade_between(network)
    _verify(network["seller"], state)

    assert current_balance(network["seller"], network["buyer"]) == Decimal("10000000.00")
    assert current_balance(network["buyer"], network["seller"]) == Decimal("-10000000.00")


@pytest.mark.django_db
@pytest.mark.parametrize("state", NOT_APPROVED)
def test_a_statement_stays_openable_from_both_sides(network, state):
    """``accounting_counterparty`` resolves through shared history, not through
    the directory, so a debt stays settleable after the relationship ends."""
    _trade_between(network)
    _verify(network["seller"], state)
    _verify(network["buyer"], state)

    assert accounting_counterparty(network["seller"], network["buyer"].id) is not None
    assert accounting_counterparty(network["buyer"], network["seller"].id) is not None
    # …while the directory correctly no longer lists them.
    assert get_colleague(network["buyer"], network["seller"].id) is None


@pytest.mark.django_db
@pytest.mark.parametrize("state", NOT_APPROVED)
def test_invoices_stay_visible_to_both_parties(network, state):
    trade = _trade_between(network)
    _verify(network["seller"], state)
    _verify(network["buyer"], state)

    assert invoices_for(network["seller"]).filter(trade=trade).exists()
    assert invoices_for(network["buyer"]).filter(trade=trade).exists()


@pytest.mark.django_db
def test_an_unapproved_stranger_with_no_history_gets_nothing(network):
    """Relaxing the rule for history must not become a way in for anyone else.

    An *approved* colleague with no history does resolve, deliberately: that is
    how a business posts the first entry against somebody it has just started
    dealing with. The rule is history **or** current eligibility, and this has
    neither."""
    stranger = make_business(name="سنگ غریبه شبکه", owner_phone="09201110003")
    _verify(stranger, Business.VerificationStatus.UNVERIFIED)

    assert accounting_counterparty(stranger, network["seller"].id) is None


@pytest.mark.django_db
def test_an_approved_colleague_with_no_history_still_resolves(network):
    """The other half of the same rule, so tightening one never quietly removes
    the other: a first ledger entry has to be postable against somebody new."""
    fresh = make_business(name="سنگ تازه شبکه", owner_phone="09201110004")
    assert accounting_counterparty(network["seller"], fresh.id) == fresh
