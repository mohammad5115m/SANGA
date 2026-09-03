from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.core.testing import expire_stock, make_business, make_item, owner_membership
from apps.inventory.freshness import StockDisplay, needs_stock_confirmation, stock_view
from apps.inventory.models import InventoryLot, VocabularyTerm
from apps.inventory.selectors import filter_needs_stock_confirmation, lots_for_business
from apps.inventory.services import (
    InventoryError,
    confirm_item_stock,
    create_draft_item,
    create_product,
    set_item_availability,
    update_item,
)
from apps.pricing.services import ensure_default_tiers, resolve_visible_prices


@pytest.fixture
def seller(db):
    ensure_default_tiers()
    return make_business(name="سنگ تست", owner_phone="09120000010")


def _stone(name="تراورتن"):
    return VocabularyTerm.objects.get(kind=VocabularyTerm.Kind.STONE_TYPE, name=name)


@pytest.mark.django_db
def test_product_name_and_lot_code_are_generated(seller):
    membership = owner_membership(seller)
    product = create_product(
        business=seller,
        membership=membership,
        stone=_stone(),
        name_suffix="  عباس‌آباد   موج‌دار ",
    )
    lot = create_draft_item(business=seller, membership=membership, product=product)

    assert product.commercial_name == "سنگ تراورتن عباس‌آباد موج‌دار"
    assert lot.lot_code.startswith("T-")
    assert len(lot.lot_code.removeprefix("T-")) == 6


@pytest.mark.django_db
def test_one_product_cannot_have_two_inventory_items(seller):
    membership = owner_membership(seller)
    product = create_product(business=seller, membership=membership, stone=_stone())
    create_draft_item(business=seller, membership=membership, product=product)
    with pytest.raises(InventoryError):
        create_draft_item(business=seller, membership=membership, product=product)


@pytest.mark.django_db
def test_lot_code_is_immutable(seller):
    lot = make_item(seller)
    lot.lot_code = "T-CHANGED"
    with pytest.raises(ValidationError):
        lot.save()


@pytest.mark.django_db
def test_price_channels_remain_isolated(seller):
    item = make_item(seller, b2b="1500000", b2c="2200000")
    owner = resolve_visible_prices(item, "owner_staff", can_view_prices=True)
    colleague = resolve_visible_prices(item, "b2b_partner")
    public = resolve_visible_prices(item, "b2c_public")
    assert set(owner) == {"b2b", "b2c"}
    assert set(colleague) == {"b2b"}
    assert set(public) == {"b2c"}


@pytest.mark.django_db
def test_null_quantity_is_inquiry_and_never_stale(seller):
    item = make_item(seller, available_sqm=None)
    view = stock_view(item)
    assert view.display == StockDisplay.INQUIRY
    assert view.quantity_sqm is None
    assert needs_stock_confirmation(item) is False


@pytest.mark.django_db
def test_expired_number_becomes_inquiry_without_hiding_item(seller):
    item = make_item(seller, available_sqm="650", stock_valid_for_days=3)
    expire_stock(item)
    view = stock_view(item)
    assert view.display == StockDisplay.INQUIRY
    assert view.needs_confirmation is True
    assert item.is_visible is True
    assert item.available_sqm == Decimal("650.000")


@pytest.mark.django_db
def test_one_click_reconfirmation_restores_exact_display(seller):
    item = make_item(seller, available_sqm="650", stock_valid_for_days=3)
    expire_stock(item)
    confirm_item_stock(
        lot=item,
        membership=owner_membership(seller),
        available_sqm=item.available_sqm,
        stock_valid_for_days=item.stock_valid_for_days,
    )
    item.refresh_from_db()
    assert stock_view(item).display == StockDisplay.EXACT


@pytest.mark.django_db
def test_needs_confirmation_query_only_flags_stale_numbers(seller):
    make_item(seller, lot_code="T-FRESH1")
    stale = make_item(seller, lot_code="T-STALE1", stock_valid_for_days=2)
    expire_stock(stale)
    make_item(seller, lot_code="T-INQ001", available_sqm=None)
    codes = set(
        filter_needs_stock_confirmation(lots_for_business(seller)).values_list("lot_code", flat=True)
    )
    assert codes == {"T-STALE1"}


@pytest.mark.django_db
def test_unavailable_is_not_deleted_or_unpublished(seller):
    item = make_item(seller)
    set_item_availability(lot=item, membership=owner_membership(seller), available=False)
    item.refresh_from_db()
    assert item.availability_status == InventoryLot.Availability.UNAVAILABLE
    assert item.is_visible is True
    assert item.deleted_at is None


@pytest.mark.django_db
def test_rejected_price_rolls_back_the_unified_edit(seller):
    item = make_item(seller, b2b="1000")
    with pytest.raises(InventoryError):
        update_item(
            lot=item,
            membership=owner_membership(seller),
            fields={"processing_type": "چرمی"},
            b2b_price={"mode": "fixed", "amount": None},
        )
    item.refresh_from_db()
    assert item.processing_type == "ساب خورده"
