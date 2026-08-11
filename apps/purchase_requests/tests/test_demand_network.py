from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.businesses.models import BusinessMembership
from apps.businesses.services import add_warehouse, create_business_for_owner
from apps.inventory.models import InventoryLot, Product
from apps.matching.services import RuleBasedMatchingService, persist_matches
from apps.pricing.services import ensure_default_tiers, set_lot_prices
from apps.purchase_requests.models import PurchaseOffer, PurchaseRequest
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

    wh = add_warehouse(business=seller, name="انبار", city="محلات", is_default=True)
    product = Product.objects.create(
        business=seller,
        commercial_name="تراورتن سفید تقاضا",
        stone_type="تراورتن",
        primary_color="سفید",
    )
    lot = InventoryLot.objects.create(
        business=seller,
        product=product,
        warehouse=wh,
        lot_code="DEM-1",
        status=InventoryLot.Status.AVAILABLE,
        visibility=InventoryLot.Visibility.ALL_PARTNERS,
        available_sqm=Decimal("200"),
        original_sqm=Decimal("200"),
        thickness_mm=Decimal("20"),
        grade="ممتاز",
        inventory_confirmed_at=timezone.now(),
    )
    set_lot_prices(lot=lot, b2b_amount=Decimal("1800000"), b2c_amount=Decimal("2500000"))
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


@pytest.mark.django_db
def test_rule_based_matching_scores_compatible_lot(demand_setup):
    pr = create_purchase_request(
        business=demand_setup["buyer"],
        membership=demand_setup["buyer_m"],
        title="نیاز تراورتن سفید نما",
        stone_type="تراورتن",
        color="سفید",
        required_qty_sqm=Decimal("100"),
        thickness_mm=Decimal("20"),
        acceptable_grade="ممتاز",
        budget_amount=Decimal("2000000"),
        destination_city="محلات",
        similar_accepted=True,
    )
    matches = RuleBasedMatchingService().find_matches(pr)
    assert matches
    assert matches[0].lot.id == demand_setup["lot"].id
    assert matches[0].score >= 50
    assert pr.match_results.exists()


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
def test_insufficient_qty_does_not_match(demand_setup):
    lot = demand_setup["lot"]
    lot.available_sqm = Decimal("10")
    lot.save(update_fields=["available_sqm"])
    pr = PurchaseRequest.objects.create(
        business=demand_setup["buyer"],
        created_by=demand_setup["buyer_user"],
        title="نیاز زیاد",
        stone_type="تراورتن",
        color="سفید",
        required_qty_sqm=Decimal("100"),
        similar_accepted=True,
    )
    matches = persist_matches(pr)
    assert matches == []


@pytest.mark.django_db
def test_rematch_prunes_stale_matches(demand_setup):
    from apps.matching.models import MatchResult

    pr = create_purchase_request(
        business=demand_setup["buyer"],
        membership=demand_setup["buyer_m"],
        title="نیاز تراورتن",
        stone_type="تراورتن",
        color="سفید",
        required_qty_sqm=Decimal("100"),
        similar_accepted=True,
    )
    assert MatchResult.objects.filter(purchase_request=pr, lot=demand_setup["lot"]).exists()

    # Lot no longer qualifies (too little quantity); rematch must remove it.
    lot = demand_setup["lot"]
    lot.available_sqm = Decimal("5")
    lot.save(update_fields=["available_sqm"])
    persist_matches(pr)
    assert not MatchResult.objects.filter(purchase_request=pr, lot=lot).exists()


@pytest.mark.django_db
def test_seller_notifications_not_duplicated_on_rematch(demand_setup):
    from apps.matching.models import MatchResult
    from apps.notifications.models import Notification
    from apps.purchase_requests.services import _notify_potential_sellers

    pr = create_purchase_request(
        business=demand_setup["buyer"],
        membership=demand_setup["buyer_m"],
        title="نیاز تراورتن",
        stone_type="تراورتن",
        color="سفید",
        required_qty_sqm=Decimal("100"),
        similar_accepted=True,
    )
    count_after_create = Notification.objects.filter(user=demand_setup["seller_user"]).count()
    assert count_after_create >= 1
    assert not MatchResult.objects.filter(purchase_request=pr, notified=False).exists()

    # Re-running notification must not resend for already-notified matches.
    _notify_potential_sellers(pr)
    assert Notification.objects.filter(user=demand_setup["seller_user"]).count() == count_after_create
