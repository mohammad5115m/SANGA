from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from apps.businesses.models import BusinessMembership
from apps.businesses.permissions import PRICES_EDIT, defaults_for_role
from apps.businesses.services import add_warehouse, create_business_for_owner

User = get_user_model()


@pytest.mark.django_db
def test_owner_membership_and_warehouse_scoped():
    owner_a = User.objects.create_user(phone="09120000001", full_name="مالک الف")
    owner_b = User.objects.create_user(phone="09120000002", full_name="مالک ب")

    business_a = create_business_for_owner(owner=owner_a, name="سنگ الف", city="اصفهان")
    business_b = create_business_for_owner(owner=owner_b, name="سنگ ب", city="یزد")

    wh_a = add_warehouse(business=business_a, name="انبار مرکزی", city="اصفهان", is_default=True)
    wh_b = add_warehouse(business=business_b, name="انبار مرکزی", city="یزد", is_default=True)

    assert wh_a.business_id == business_a.id
    assert wh_b.business_id == business_b.id
    assert business_a.warehouses.count() == 1
    assert business_b.warehouses.count() == 1

    membership_a = BusinessMembership.objects.get(user=owner_a, business=business_a)
    assert membership_a.has_capability(PRICES_EDIT)
    assert PRICES_EDIT in defaults_for_role("owner")

    # Cross-tenant: owner A must not have membership on B
    assert not BusinessMembership.objects.filter(user=owner_a, business=business_b).exists()
