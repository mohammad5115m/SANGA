from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.businesses.models import BusinessMembership
from apps.businesses.permissions import INQUIRIES_RESPOND, RESERVATIONS_MANAGE
from apps.businesses.services import add_warehouse, create_business_for_owner
from apps.inventory.models import InventoryLot, Product
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
    # Viewers may browse the demand board but hold no inquiries.respond.
    viewer_m = BusinessMembership.objects.create(
        user=viewer_user,
        business=buyer,
        role=BusinessMembership.Role.VIEWER,
        status=BusinessMembership.Status.ACTIVE,
    )

    warehouse = add_warehouse(business=seller, name="انبار", is_default=True)
    product = Product.objects.create(
        business=seller, commercial_name="تراورتن", stone_type="تراورتن"
    )
    lot = InventoryLot.objects.create(
        business=seller,
        product=product,
        warehouse=warehouse,
        lot_code="PR-1",
        status=InventoryLot.Status.AVAILABLE,
        visibility=InventoryLot.Visibility.PUBLIC,
        available_sqm=Decimal("100"),
        original_sqm=Decimal("100"),
        inventory_confirmed_at=timezone.now(),
    )
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
    """The buyer needs the capability that governs their own purchase requests,
    not the seller-side reservations.manage that used to be demanded here.
    """
    pr = _make_request(demand)
    offer = _make_offer(demand, pr)

    buyer_staff = BusinessMembership.objects.create(
        user=User.objects.create_user(phone="09128880005", full_name="کارمند خریدار"),
        business=demand["buyer"],
        role=BusinessMembership.Role.STAFF,
        status=BusinessMembership.Status.ACTIVE,
        # Holds inquiries.respond but explicitly not reservations.manage.
        permissions=[INQUIRIES_RESPOND],
    )
    assert not buyer_staff.has_capability(RESERVATIONS_MANAGE)

    decide_offer(offer=offer, membership=buyer_staff, accept=True)
    offer.refresh_from_db()
    assert offer.status == PurchaseOffer.Status.ACCEPTED


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


# --- lot scoping -----------------------------------------------------------


def test_offer_form_only_offers_the_sellers_own_lots(demand):
    buyer_lot = InventoryLot.objects.create(
        business=demand["buyer"],
        product=Product.objects.create(
            business=demand["buyer"], commercial_name="سنگ خریدار", stone_type="گرانیت"
        ),
        warehouse=add_warehouse(business=demand["buyer"], name="انبار خریدار", is_default=True),
        lot_code="BUY-1",
        status=InventoryLot.Status.AVAILABLE,
        available_sqm=Decimal("10"),
        original_sqm=Decimal("10"),
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
    buyer_lot = InventoryLot.objects.create(
        business=demand["buyer"],
        product=Product.objects.create(
            business=demand["buyer"], commercial_name="سنگ خریدار", stone_type="گرانیت"
        ),
        warehouse=add_warehouse(business=demand["buyer"], name="انبار خریدار", is_default=True),
        lot_code="BUY-2",
        status=InventoryLot.Status.AVAILABLE,
        available_sqm=Decimal("10"),
        original_sqm=Decimal("10"),
    )
    with pytest.raises(PurchaseRequestError):
        _make_offer(demand, pr, lot=buyer_lot)
    assert not PurchaseOffer.objects.exists()
