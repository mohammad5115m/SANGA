from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.businesses.models import BusinessMembership
from apps.businesses.services import add_warehouse, create_business_for_owner
from apps.inventory.forms import LotEditForm, editable_visibility_choices
from apps.inventory.freshness import FreshnessLevel, apply_freshness_transition, evaluate_freshness
from apps.inventory.models import InventoryLot, Product
from apps.inventory.selectors import get_business_lot
from apps.inventory.services import (
    InventoryError,
    create_draft_lot,
    create_or_get_product,
    update_lot_prices,
)
from apps.pricing.services import ensure_default_tiers, resolve_visible_prices

User = get_user_model()


@pytest.fixture
def owner_business(db):
    owner = User.objects.create_user(phone="09120000010", full_name="مالک تست")
    business = create_business_for_owner(owner=owner, name="سنگ تست الف", city="تهران")
    warehouse = add_warehouse(business=business, name="انبار ۱", city="تهران", is_default=True)
    membership = BusinessMembership.objects.get(user=owner, business=business)
    ensure_default_tiers()
    return owner, business, warehouse, membership


@pytest.mark.django_db
def test_create_lot_and_prices_audience_isolation(owner_business):
    owner, business, warehouse, membership = owner_business
    product = create_or_get_product(
        business=business,
        membership=membership,
        commercial_name="تراورتن تست",
        stone_type="تراورتن",
        primary_color="کرم",
    )
    lot = create_draft_lot(
        business=business,
        membership=membership,
        product=product,
        warehouse=warehouse,
        available_sqm=Decimal("100.000"),
    )
    update_lot_prices(
        lot=lot,
        membership=membership,
        b2b_amount=Decimal("1500000"),
        b2c_amount=Decimal("2200000"),
    )

    owner_prices = resolve_visible_prices(lot, "owner_staff", can_view_prices=True)
    b2b_prices = resolve_visible_prices(lot, "b2b_partner")
    b2c_prices = resolve_visible_prices(lot, "b2c_public")

    assert set(owner_prices.keys()) == {"b2b", "b2c"}
    assert set(b2b_prices.keys()) == {"b2b"}
    assert set(b2c_prices.keys()) == {"b2c"}
    assert "b2b" not in b2c_prices
    assert b2c_prices["b2c"].amount == Decimal("2200000")


@pytest.mark.django_db
def test_cross_tenant_lot_access_denied(owner_business):
    _owner, business_a, warehouse_a, membership_a = owner_business
    owner_b = User.objects.create_user(phone="09120000011")
    business_b = create_business_for_owner(owner=owner_b, name="سنگ تست ب", city="یزد")

    product = create_or_get_product(
        business=business_a,
        membership=membership_a,
        commercial_name="محصول الف",
    )
    lot = create_draft_lot(
        business=business_a,
        membership=membership_a,
        product=product,
        warehouse=warehouse_a,
        available_sqm=Decimal("10"),
    )

    assert get_business_lot(business_b, lot.id) is None


@pytest.mark.django_db
def test_freshness_transitions(owner_business):
    _owner, business, warehouse, membership = owner_business
    product = Product.objects.create(business=business, commercial_name="سنگ کهنه")
    lot = InventoryLot.objects.create(
        business=business,
        product=product,
        warehouse=warehouse,
        lot_code="OLD-1",
        status=InventoryLot.Status.AVAILABLE,
        available_sqm=Decimal("20"),
        original_sqm=Decimal("20"),
        inventory_confirmed_at=timezone.now() - timedelta(days=10),
    )
    info = evaluate_freshness(lot)
    assert info.level == FreshnessLevel.NEEDS_CONFIRMATION
    apply_freshness_transition(lot)
    lot.refresh_from_db()
    assert lot.status == InventoryLot.Status.NEEDS_CONFIRMATION

    lot.inventory_confirmed_at = timezone.now() - timedelta(days=30)
    lot.status = InventoryLot.Status.AVAILABLE
    lot.save(update_fields=["inventory_confirmed_at", "status"])
    apply_freshness_transition(lot)
    lot.refresh_from_db()
    assert lot.status == InventoryLot.Status.HIDDEN


@pytest.mark.django_db
def test_other_business_cannot_price_lot(owner_business):
    _owner, business_a, warehouse_a, membership_a = owner_business
    owner_b = User.objects.create_user(phone="09120000012")
    business_b = create_business_for_owner(owner=owner_b, name="کسب ب", city="قم")
    membership_b = BusinessMembership.objects.get(user=owner_b, business=business_b)

    product = create_or_get_product(
        business=business_a,
        membership=membership_a,
        commercial_name="محصول الف",
    )
    lot = create_draft_lot(
        business=business_a,
        membership=membership_a,
        product=product,
        warehouse=warehouse_a,
        available_sqm=Decimal("5"),
    )
    with pytest.raises(InventoryError):
        update_lot_prices(
            lot=lot,
            membership=membership_b,
            b2b_amount=Decimal("1"),
            b2c_amount=Decimal("2"),
        )


@pytest.mark.django_db
def test_visibility_offers_exactly_three_levels(owner_business):
    _owner, business, warehouse, _membership = owner_business

    offered = [value for value, _label in editable_visibility_choices()]
    assert offered == [
        InventoryLot.Visibility.PRIVATE,
        InventoryLot.Visibility.COLLEAGUES,
        InventoryLot.Visibility.PUBLIC,
    ]

    product = Product.objects.create(business=business, commercial_name="سنگ سه‌سطحی")
    lot = InventoryLot.objects.create(
        business=business,
        product=product,
        warehouse=warehouse,
        lot_code="VIS-1",
        status=InventoryLot.Status.AVAILABLE,
        visibility=InventoryLot.Visibility.COLLEAGUES,
        available_sqm=Decimal("10"),
        original_sqm=Decimal("10"),
    )
    form = LotEditForm(instance=lot, business=business)
    assert [value for value, _label in form.fields["visibility"].choices] == offered
