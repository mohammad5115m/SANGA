"""History outlives the network relationship.

The colleague directory answers "who may I trade with today". A statement, an
invoice and an outstanding debt answer "who did I trade with". Resolving the
second question through the first meant a debtor's statement started returning
404 the moment the platform suspended them, and the money owed became
unsettleable — the debt was still real, it just had no page.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.accounting.models import LedgerEntry
from apps.accounting.selectors import accounting_counterparty, current_balance
from apps.accounting.services import post_manual_entry
from apps.businesses.models import Business
from apps.core.testing import make_business, make_item, make_product, owner_membership
from apps.pricing.services import ensure_default_tiers
from apps.trading.services import record_direct_sale


@pytest.fixture
def debt(db):
    ensure_default_tiers()
    seller = make_business(name="سنگ طلبکار", owner_phone="09341110001")
    debtor = make_business(name="سنگ بدهکار", owner_phone="09341110002")
    item = make_item(
        seller,
        product=make_product(seller, commercial_name="تراورتن بدهی"),
        lot_code="HS-1",
        b2b="1000000",
    )
    record_direct_sale(
        seller_business=seller,
        membership=owner_membership(seller),
        item=item,
        quantity_sqm=Decimal("30"),
        unit_price=Decimal("1000000"),
        buyer_business=debtor,
    )
    return {"seller": seller, "debtor": debtor, "membership": owner_membership(seller)}


def _suspend(business: Business) -> None:
    business.status = Business.Status.SUSPENDED
    business.save(update_fields=["status"])


def _login(client, business: Business) -> None:
    client.force_login(business.memberships.get(role="owner").user)
    session = client.session
    session["current_business_id"] = str(business.id)
    session.save()


@pytest.mark.django_db
def test_a_suspended_debtor_still_resolves_for_accounting(debt):
    _suspend(debt["debtor"])
    assert accounting_counterparty(debt["seller"], debt["debtor"].id) is not None


@pytest.mark.django_db
def test_a_suspended_debtor_leaves_the_colleague_directory(debt):
    """The two questions must keep separate answers, or this fix is just a hole."""
    from apps.businesses.directory import get_colleague

    _suspend(debt["debtor"])
    assert get_colleague(debt["seller"], debt["debtor"].id) is None


@pytest.mark.django_db
def test_a_suspended_business_with_no_shared_history_never_resolves(debt):
    """The tenant boundary this resolver exists to keep.

    An *active* stranger does resolve — every active Business is a colleague, and
    a first entry has to be possible. What must not resolve is a Business that is
    neither currently eligible nor part of this business's records.
    """
    stranger = make_business(name="سنگ غریبه", owner_phone="09341110009")
    _suspend(stranger)
    assert accounting_counterparty(debt["seller"], stranger.id) is None


@pytest.mark.django_db
def test_the_statement_of_a_suspended_debtor_still_opens(client, debt):
    _suspend(debt["debtor"])
    _login(client, debt["seller"])

    response = client.get(reverse("accounting:statement", kwargs={"business_id": debt["debtor"].id}))
    assert response.status_code == 200
    assert "30,000,000" in response.content.decode()


@pytest.mark.django_db
def test_the_statement_of_a_suspended_debtor_still_prints(client, debt):
    _suspend(debt["debtor"])
    _login(client, debt["seller"])

    response = client.get(reverse("accounting:print", kwargs={"business_id": debt["debtor"].id}))
    assert response.status_code == 200


@pytest.mark.django_db
def test_a_settlement_can_be_recorded_against_a_suspended_debtor(debt):
    """The point of the whole fix: the debt stays collectable."""
    _suspend(debt["debtor"])
    colleague = accounting_counterparty(debt["seller"], debt["debtor"].id)

    post_manual_entry(
        business=debt["seller"],
        counterparty=colleague,
        membership=debt["membership"],
        entry_type=LedgerEntry.Type.PAYMENT_RECEIVED,
        amount=Decimal("30000000"),
        description="تسویه نقدی",
    )
    assert current_balance(debt["seller"], debt["debtor"]) == Decimal("0.00")


@pytest.mark.django_db
def test_a_stranger_cannot_be_posted_to_through_the_accounting_route(client, debt):
    stranger = make_business(name="سنگ غریبه", owner_phone="09341110020")
    _suspend(stranger)
    _login(client, debt["seller"])

    response = client.get(reverse("accounting:add_entry", kwargs={"business_id": stranger.id}))
    assert response.status_code == 404


@pytest.mark.django_db
def test_an_active_colleague_with_no_history_is_still_postable(debt):
    """A first entry has to be possible, so eligibility is a second way in — not
    a replacement for the historical one."""
    fresh = make_business(name="سنگ تازه", owner_phone="09341110030")
    assert accounting_counterparty(debt["seller"], fresh.id) is not None
