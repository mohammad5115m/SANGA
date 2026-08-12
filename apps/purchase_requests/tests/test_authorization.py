from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from apps.businesses.models import Business, BusinessMembership
from apps.businesses.permissions import LEADS_MANAGE
from apps.businesses.services import create_business_for_owner
from apps.core.testing import make_item, make_product
from apps.inventory.models import InventoryLot
from apps.notifications.models import Notification
from apps.purchase_requests.forms import PurchaseOfferForm
from apps.purchase_requests.models import PurchaseOffer, PurchaseRequest
from apps.purchase_requests.services import (
    PurchaseRequestError,
    close_purchase_request,
    create_purchase_request,
    decide_offer,
    submit_private_offer,
)

User = get_user_model()


@pytest.fixture
def demand(db):
    buyer_user = User.objects.create_user(phone="09128880001", full_name="خریدار")
    seller_user = User.objects.create_user(phone="09128880002", full_name="فروشنده")
    viewer_user = User.objects.create_user(phone="09128880003", full_name="بازدیدکننده")

    buyer = create_business_for_owner(owner=buyer_user, name="خریدار پروژه", city="تهران")
    seller = create_business_for_owner(owner=seller_user, name="سنگ فروشنده", city="محلات")

    buyer_m = BusinessMembership.objects.get(user=buyer_user, business=buyer)
    seller_m = BusinessMembership.objects.get(user=seller_user, business=seller)
    # Viewers may browse the demand board but hold no leads.manage.
    viewer_m = BusinessMembership.objects.create(
        user=viewer_user,
        business=buyer,
        role=BusinessMembership.Role.VIEWER,
        status=BusinessMembership.Status.ACTIVE,
    )

    product = make_product(seller, commercial_name="تراورتن")
    lot = make_item(seller, product=product, lot_code="PR-1")
    return {
        "buyer": buyer,
        "seller": seller,
        "buyer_user": buyer_user,
        "seller_user": seller_user,
        "viewer_user": viewer_user,
        "buyer_m": buyer_m,
        "seller_m": seller_m,
        "viewer_m": viewer_m,
        "lot": lot,
    }


def _make_request(demand) -> PurchaseRequest:
    return create_purchase_request(
        business=demand["buyer"],
        membership=demand["buyer_m"],
        title="تراورتن نمای پروژه",
        required_qty_sqm=Decimal("60"),
    )


def _make_offer(demand, pr, lot=None) -> PurchaseOffer:
    return submit_private_offer(
        purchase_request=pr,
        seller_business=demand["seller"],
        membership=demand["seller_m"],
        unit_price=Decimal("900000"),
        offered_qty_sqm=Decimal("60"),
        lot=lot,
    )


# --- capability gating -----------------------------------------------------


def test_creating_a_purchase_request_requires_the_capability(demand):
    with pytest.raises(PurchaseRequestError):
        create_purchase_request(
            business=demand["buyer"],
            membership=demand["viewer_m"],
            title="درخواست بدون دسترسی",
            required_qty_sqm=Decimal("10"),
        )
    assert not PurchaseRequest.objects.exists()


def test_submitting_an_offer_requires_the_capability(demand):
    pr = _make_request(demand)
    seller_viewer = BusinessMembership.objects.create(
        user=User.objects.create_user(phone="09128880004", full_name="کارمند فروشنده"),
        business=demand["seller"],
        role=BusinessMembership.Role.VIEWER,
        status=BusinessMembership.Status.ACTIVE,
    )
    with pytest.raises(PurchaseRequestError):
        submit_private_offer(
            purchase_request=pr,
            seller_business=demand["seller"],
            membership=seller_viewer,
            unit_price=Decimal("900000"),
            offered_qty_sqm=Decimal("60"),
        )
    assert not PurchaseOffer.objects.exists()


def test_closing_a_purchase_request_requires_the_capability(demand):
    pr = _make_request(demand)
    with pytest.raises(PurchaseRequestError):
        close_purchase_request(purchase_request=pr, membership=demand["viewer_m"])
    pr.refresh_from_db()
    assert pr.status != PurchaseRequest.Status.CANCELLED


def test_accepting_an_offer_uses_the_purchase_request_capability(demand):
    """Accepting is part of running your own purchase request, so ``leads.manage``
    is the only capability involved — and it is enough on its own.
    """
    pr = _make_request(demand)
    offer = _make_offer(demand, pr)

    buyer_staff = BusinessMembership.objects.create(
        user=User.objects.create_user(phone="09128880005", full_name="کارمند خریدار"),
        business=demand["buyer"],
        role=BusinessMembership.Role.STAFF,
        status=BusinessMembership.Status.ACTIVE,
        permissions=[LEADS_MANAGE],
    )

    decide_offer(offer=offer, membership=buyer_staff, accept=True)
    offer.refresh_from_db()
    assert offer.status == PurchaseOffer.Status.ACCEPTED


def test_accepting_an_offer_holds_no_stock_and_notifies_the_seller(demand):
    """Acceptance is a decision, not a hold: quantity and lot status are untouched
    and the seller is told so the trade can be settled offline.
    """
    pr = _make_request(demand)
    offer = _make_offer(demand, pr, lot=demand["lot"])
    available_before = demand["lot"].available_sqm

    decide_offer(offer=offer, membership=demand["buyer_m"], accept=True)

    demand["lot"].refresh_from_db()
    assert demand["lot"].available_sqm == available_before
    assert demand["lot"].availability_status == InventoryLot.Availability.AVAILABLE
    assert Notification.objects.filter(
        user=demand["seller_user"], title="پیشنهاد شما پذیرفته شد"
    ).exists()


def test_a_viewer_cannot_accept_an_offer(demand):
    pr = _make_request(demand)
    offer = _make_offer(demand, pr)

    with pytest.raises(PurchaseRequestError):
        decide_offer(offer=offer, membership=demand["viewer_m"], accept=True)
    offer.refresh_from_db()
    assert offer.status == PurchaseOffer.Status.SUBMITTED


def test_a_viewer_cannot_reach_the_create_screen(client, demand):
    client.force_login(demand["viewer_user"])
    response = client.get("/app/purchase-requests/new/")
    assert response.status_code == 302
    assert not PurchaseRequest.objects.exists()


def test_a_viewer_can_still_browse_the_demand_board(client, demand):
    _make_request(demand)
    client.force_login(demand["viewer_user"])
    assert client.get("/app/purchase-requests/").status_code == 200
    assert client.get("/app/purchase-requests/network/").status_code == 200


def test_a_viewer_posting_an_offer_is_refused_by_the_view(client, demand):
    pr = _make_request(demand)
    seller_viewer_user = User.objects.create_user(phone="09128880006", full_name="بازدید فروشنده")
    BusinessMembership.objects.create(
        user=seller_viewer_user,
        business=demand["seller"],
        role=BusinessMembership.Role.VIEWER,
        status=BusinessMembership.Status.ACTIVE,
    )
    client.force_login(seller_viewer_user)
    response = client.post(
        f"/app/purchase-requests/network/{pr.id}/",
        {"unit_price": "900000", "offered_qty_sqm": "60"},
    )
    assert response.status_code == 302
    assert not PurchaseOffer.objects.exists()


# --- active-business gating ------------------------------------------------


def _suspend(business: Business) -> None:
    business.status = Business.Status.SUSPENDED
    business.save(update_fields=["status"])


def test_a_suspended_business_cannot_submit_an_offer(demand):
    pr = _make_request(demand)
    _suspend(demand["seller"])

    with pytest.raises(PurchaseRequestError):
        _make_offer(demand, pr)
    assert not PurchaseOffer.objects.exists()


def test_a_suspended_business_receives_no_offers(demand):
    _make_request(demand)
    _suspend(demand["buyer"])
    pr = PurchaseRequest.objects.get(business=demand["buyer"])

    with pytest.raises(PurchaseRequestError):
        _make_offer(demand, pr)
    assert not PurchaseOffer.objects.exists()


def test_an_active_business_still_submits_offers(demand):
    pr = _make_request(demand)
    offer = _make_offer(demand, pr)
    assert offer.status == PurchaseOffer.Status.SUBMITTED


def test_a_suspended_sellers_offer_cannot_be_accepted(demand):
    """The offer predates the suspension; accepting it would still commit the
    buyer to a counterparty that is no longer in the network.
    """
    pr = _make_request(demand)
    offer = _make_offer(demand, pr)
    _suspend(demand["seller"])
    offer = PurchaseOffer.objects.select_related("seller_business", "purchase_request").get(pk=offer.pk)

    with pytest.raises(PurchaseRequestError):
        decide_offer(offer=offer, membership=demand["buyer_m"], accept=True)
    offer.refresh_from_db()
    assert offer.status == PurchaseOffer.Status.SUBMITTED


# --- lot scoping -----------------------------------------------------------


def test_offer_form_only_offers_the_sellers_own_lots(demand):
    buyer_lot = make_item(
        demand["buyer"],
        product=make_product(demand["buyer"], commercial_name="سنگ خریدار", stone_type="گرانیت"),
        lot_code="BUY-1",
    )
    form = PurchaseOfferForm(
        {"unit_price": "900000", "offered_qty_sqm": "60", "lot": str(buyer_lot.id)},
        business=demand["seller"],
    )
    assert not form.is_valid()
    assert "lot" in form.errors

    ok = PurchaseOfferForm(
        {"unit_price": "900000", "offered_qty_sqm": "60", "lot": str(demand["lot"].id)},
        business=demand["seller"],
    )
    assert ok.is_valid(), ok.errors


def test_service_refuses_a_lot_the_seller_does_not_own(demand):
    pr = _make_request(demand)
    buyer_lot = make_item(
        demand["buyer"],
        product=make_product(demand["buyer"], commercial_name="سنگ خریدار", stone_type="گرانیت"),
        lot_code="BUY-2",
    )
    with pytest.raises(PurchaseRequestError):
        _make_offer(demand, pr, lot=buyer_lot)
    assert not PurchaseOffer.objects.exists()
