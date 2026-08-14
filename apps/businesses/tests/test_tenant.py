from __future__ import annotations

import pytest

from apps.businesses.models import BusinessMembership
from apps.businesses.permissions import PRICES_EDIT, defaults_for_role
from apps.core.testing import make_business, make_item, owner_membership
from apps.inventory.selectors import get_business_lot, lots_for_business


@pytest.mark.django_db
def test_owner_membership_is_scoped_to_its_own_business():
    business_a = make_business(name="سنگ الف", owner_phone="09120000001", city="اصفهان")
    business_b = make_business(name="سنگ ب", owner_phone="09120000002", city="یزد")

    membership_a = owner_membership(business_a)
    assert membership_a.has_capability(PRICES_EDIT)
    assert PRICES_EDIT in defaults_for_role("owner")

    owner_a = membership_a.user
    assert not BusinessMembership.objects.filter(user=owner_a, business=business_b).exists()


@pytest.mark.django_db
def test_inventory_is_scoped_to_its_own_business():
    business_a = make_business(name="سنگ الف", owner_phone="09120000003")
    business_b = make_business(name="سنگ ب", owner_phone="09120000004")

    item_a = make_item(business_a, lot_code="A-1")
    item_b = make_item(business_b, lot_code="B-1")

    assert list(lots_for_business(business_a)) == [item_a]
    assert list(lots_for_business(business_b)) == [item_b]
    assert get_business_lot(business_b, item_a.id) is None


@pytest.mark.django_db
def test_inventory_has_no_warehouse_or_location_fields():
    business = make_business(name="سنگ محل", owner_phone="09120000005", city="محلات", province="مرکزی")
    item = make_item(business, lot_code="T-LOC001")
    field_names = {field.name for field in item._meta.get_fields()}
    assert "warehouse" not in field_names
    assert not {"location_city", "location_province", "location_address"} & field_names
