from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.businesses.models import Business, BusinessMembership
from apps.businesses.services import create_business_for_owner
from apps.core.testing import make_item, make_product
from apps.marketplace.selectors import marketplace_lots_for
from apps.pricing.services import ensure_default_tiers
from apps.purchase_requests.models import PurchaseOffer
from apps.purchase_requests.selectors import (
    get_network_request,
    get_own_request,
    my_purchase_requests,
    network_purchase_requests,
)
from apps.purchase_requests.services import create_purchase_request, submit_private_offer

User = get_user_model()


@pytest.fixture
def demand_setup(db):
    ensure_default_tiers()
    buyer_user = User.objects.create_user(phone="09127770001", full_name="خریدار")
    seller_user = User.objects.create_user(phone="09127770002", full_name="فروشنده")
    other_seller_user = User.objects.create_user(phone="09127770003", full_name="فروشنده ۲")

    buyer = create_business_for_owner(owner=buyer_user, name="خریدار تقاضا", city="تهران")
    seller = create_business_for_owner(owner=seller_user, name="فروشنده عرضه", city="محلات")
    other = create_business_for_owner(owner=other_seller_user, name="فروشنده دیگر", city="یزد")

    buyer_m = BusinessMembership.objects.get(user=buyer_user, business=buyer)
    seller_m = BusinessMembership.objects.get(user=seller_user, business=seller)
    other_m = BusinessMembership.objects.get(user=other_seller_user, business=other)

    product = make_product(
        seller,
        commercial_name="تراورتن سفید تقاضا",
        stone_type="تراورتن",
        primary_color="سفید",
    )
    lot = make_item(
        seller,
        product=product,
        lot_code="DEM-1",
        available_sqm="200",
        thickness_mm=Decimal("20"),
        grade="ممتاز",
        b2b="1800000",
        b2c="2500000",
    )
    return {
        "buyer": buyer,
        "seller": seller,
        "other": other,
        "buyer_user": buyer_user,
        "seller_user": seller_user,
        "other_user": other_seller_user,
        "buyer_m": buyer_m,
        "seller_m": seller_m,
        "other_m": other_m,
        "lot": lot,
    }


def _demand(demand_setup, title="نیاز تراورتن سفید نما"):
    return create_purchase_request(
        business=demand_setup["buyer"],
        membership=demand_setup["buyer_m"],
        title=title,
        stone_type="تراورتن",
        color="سفید",
        required_qty_sqm=Decimal("100"),
        similar_accepted=True,
    )


@pytest.mark.django_db
def test_demand_is_visible_to_other_businesses_and_not_to_its_author(demand_setup):
    """Posted demand reaches the whole network without any partnership, and a
    business never sees its own request on the board it browses.
    """
    pr = _demand(demand_setup)

    assert pr.id in {item.id for item in network_purchase_requests(demand_setup["seller"])}
    assert pr.id in {item.id for item in network_purchase_requests(demand_setup["other"])}
    assert pr.id not in {item.id for item in network_purchase_requests(demand_setup["buyer"])}


@pytest.mark.django_db
def test_a_suspended_viewer_sees_an_empty_demand_board(demand_setup):
    pr = _demand(demand_setup)
    seller = demand_setup["seller"]
    seller.status = Business.Status.SUSPENDED
    seller.save(update_fields=["status"])

    assert list(network_purchase_requests(seller)) == []
    # Nor by UUID: a suspended business cannot reach network demand at all.
    assert get_network_request(seller, pr.id) is None
    # The other, still active business is unaffected.
    assert pr.id in {item.id for item in network_purchase_requests(demand_setup["other"])}


@pytest.mark.django_db
def test_a_suspended_businesss_demand_leaves_the_board_for_everyone(demand_setup):
    pr = _demand(demand_setup)
    buyer = demand_setup["buyer"]
    buyer.status = Business.Status.SUSPENDED
    buyer.save(update_fields=["status"])

    assert list(network_purchase_requests(demand_setup["seller"])) == []
    assert get_network_request(demand_setup["other"], pr.id) is None


@pytest.mark.django_db
def test_a_suspended_business_still_reads_its_own_purchase_requests(demand_setup):
    """Suspension is about the shared network, not about a business's own books."""
    pr = _demand(demand_setup)
    buyer = demand_setup["buyer"]
    buyer.status = Business.Status.SUSPENDED
    buyer.save(update_fields=["status"])

    assert pr.id in {item.id for item in my_purchase_requests(buyer)}
    assert get_own_request(buyer, pr.id) is not None


@pytest.mark.django_db
def test_buyer_finds_supply_from_another_business_in_the_marketplace(demand_setup):
    """The replacement for automatic matching: the buyer browses colleague-visible
    supply directly, with no partnership and no persisted match.
    """
    lots = marketplace_lots_for(demand_setup["buyer"])
    assert demand_setup["lot"].id in {lot.id for lot in lots}


@pytest.mark.django_db
def test_an_unpublished_item_leaves_the_marketplace_immediately(demand_setup):
    lot = demand_setup["lot"]
    lot.is_visible = False
    lot.save(update_fields=["is_visible"])

    assert lot.id not in {item.id for item in marketplace_lots_for(demand_setup["buyer"])}


@pytest.mark.django_db
def test_a_deleted_item_leaves_the_marketplace_immediately(demand_setup):
    lot = demand_setup["lot"]
    lot.deleted_at = timezone.now()
    lot.save(update_fields=["deleted_at"])

    assert lot.id not in {item.id for item in marketplace_lots_for(demand_setup["buyer"])}


@pytest.mark.django_db
def test_private_offers_not_visible_to_other_sellers(client, demand_setup):
    pr = create_purchase_request(
        business=demand_setup["buyer"],
        membership=demand_setup["buyer_m"],
        title="درخواست خصوصی تست",
        stone_type="تراورتن",
        color="سفید",
        required_qty_sqm=Decimal("50"),
        similar_accepted=True,
    )
    submit_private_offer(
        purchase_request=pr,
        seller_business=demand_setup["seller"],
        membership=demand_setup["seller_m"],
        unit_price=Decimal("1750000"),
        offered_qty_sqm=Decimal("60"),
        message="پیشنهاد محرمانه آلفا",
        lot=demand_setup["lot"],
    )

    # Other seller must not see the private offer content.
    client.force_login(demand_setup["other_user"])
    session = client.session
    session["current_business_id"] = str(demand_setup["other"].id)
    session.save()
    response = client.get(reverse("purchase_requests:network_detail", kwargs={"pr_id": pr.id}))
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "1750000" not in content.replace(",", "")
    assert "پیشنهاد محرمانه آلفا" not in content
    assert "فروشنده عرضه" not in content or "پیشنهاد شما" not in content

    # Buyer can see the private offer on own detail page.
    client.force_login(demand_setup["buyer_user"])
    session = client.session
    session["current_business_id"] = str(demand_setup["buyer"].id)
    session.save()
    response = client.get(reverse("purchase_requests:detail", kwargs={"pr_id": pr.id}))
    content = response.content.decode("utf-8")
    assert response.status_code == 200
    assert "1750000" in content.replace(",", "")
    assert "پیشنهاد محرمانه آلفا" in content


@pytest.mark.django_db
def test_accepted_offer_offers_the_buyer_a_trade_recording_link(client, demand_setup):
    pr = _demand(demand_setup, title="نیاز برای ثبت معامله")
    offer = submit_private_offer(
        purchase_request=pr,
        seller_business=demand_setup["seller"],
        membership=demand_setup["seller_m"],
        unit_price=Decimal("1800000"),
        offered_qty_sqm=Decimal("100"),
        lot=demand_setup["lot"],
    )
    offer.status = PurchaseOffer.Status.ACCEPTED
    offer.save(update_fields=["status", "updated_at"])

    client.force_login(demand_setup["buyer_user"])
    session = client.session
    session["current_business_id"] = str(demand_setup["buyer"].id)
    session.save()
    body = client.get(reverse("purchase_requests:detail", kwargs={"pr_id": pr.id})).content.decode()
    assert f"/app/accounting/record-trade/?offer={offer.id}" in body
