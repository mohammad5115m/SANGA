from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.businesses.models import BusinessMembership
from apps.businesses.services import add_warehouse, create_business_for_owner
from apps.inventory.models import InventoryLot, Product
from apps.pricing.services import ensure_default_tiers, set_lot_prices

User = get_user_model()


def _make_lot(seller, warehouse, *, code: str, qty: Decimal) -> InventoryLot:
    product = Product.objects.create(
        business=seller,
        commercial_name=f"تراورتن {code}",
        stone_type="تراورتن",
        primary_color="سفید",
    )
    lot = InventoryLot.objects.create(
        business=seller,
        product=product,
        warehouse=warehouse,
        lot_code=code,
        status=InventoryLot.Status.AVAILABLE,
        visibility=InventoryLot.Visibility.ALL_PARTNERS,
        available_sqm=qty,
        original_sqm=qty,
        inventory_confirmed_at=timezone.now(),
    )
    set_lot_prices(lot=lot, b2b_amount=Decimal("1000000"), b2c_amount=Decimal("1500000"))
    return lot


@pytest.fixture
def reservation_setup(db):
    ensure_default_tiers()
    seller_user = User.objects.create_user(phone="09120000001", full_name="فروشنده")
    buyer_user = User.objects.create_user(phone="09120000002", full_name="خریدار")
    other_user = User.objects.create_user(phone="09120000003", full_name="غریبه")
    viewer_user = User.objects.create_user(phone="09120000004", full_name="بازدیدکننده")

    seller = create_business_for_owner(owner=seller_user, name="فروشنده سنگ", city="محلات")
    buyer = create_business_for_owner(owner=buyer_user, name="خریدار سنگ", city="تهران")
    other = create_business_for_owner(owner=other_user, name="کسب‌وکار سوم", city="یزد")

    seller_m = BusinessMembership.objects.get(user=seller_user, business=seller)
    buyer_m = BusinessMembership.objects.get(user=buyer_user, business=buyer)
    other_m = BusinessMembership.objects.get(user=other_user, business=other)
    # A viewer on the seller: has reservations.view but NOT reservations.manage.
    seller_viewer_m = BusinessMembership.objects.create(
        user=viewer_user,
        business=seller,
        role=BusinessMembership.Role.VIEWER,
        status=BusinessMembership.Status.ACTIVE,
    )

    warehouse = add_warehouse(business=seller, name="انبار", city="محلات", is_default=True)
    lot = _make_lot(seller, warehouse, code="RES-1", qty=Decimal("100"))

    return {
        "seller": seller,
        "buyer": buyer,
        "other": other,
        "seller_user": seller_user,
        "buyer_user": buyer_user,
        "other_user": other_user,
        "viewer_user": viewer_user,
        "seller_m": seller_m,
        "buyer_m": buyer_m,
        "other_m": other_m,
        "seller_viewer_m": seller_viewer_m,
        "warehouse": warehouse,
        "lot": lot,
    }
