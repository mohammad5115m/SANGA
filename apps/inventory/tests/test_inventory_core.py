from __future__ import annotations

from decimal import Decimal

import pytest

from apps.core.testing import (
    expire_stock,
    make_business,
    make_item,
    make_product,
    make_user,
    owner_membership,
)
from apps.inventory.freshness import StockDisplay, needs_stock_confirmation, stock_view
from apps.inventory.models import InventoryLot
from apps.inventory.selectors import filter_needs_stock_confirmation, get_business_lot, lots_for_business
from apps.inventory.services import (
    InventoryError,
    confirm_item_stock,
    create_draft_item,
    create_or_get_product,
    set_item_availability,
    update_item,
)
from apps.pricing.services import ensure_default_tiers, resolve_visible_prices


@pytest.fixture
def seller(db):
    business = make_business(name="سنگ تست الف", owner_phone="09120000010", city="تهران")
    ensure_default_tiers()
    return business


@pytest.mark.django_db
def test_create_item_and_prices_audience_isolation(seller):
    membership = owner_membership(seller)
    product = create_or_get_product(
        business=seller,
        membership=membership,
        commercial_name="تراورتن تست",
        stone_type="تراورتن",
        primary_color="کرم",
    )
    item = create_draft_item(
        business=seller,
        membership=membership,
        product=product,
        available_sqm=Decimal("100.000"),
    )
    update_item(
        lot=item,
        membership=membership,
        b2b_price={"mode": "fixed", "amount": Decimal("1500000")},
        b2c_price={"mode": "fixed", "amount": Decimal("2200000")},
    )

    owner_prices = resolve_visible_prices(item, "owner_staff", can_view_prices=True)
    b2b_prices = resolve_visible_prices(item, "b2b_partner")
    b2c_prices = resolve_visible_prices(item, "b2c_public")

    assert set(owner_prices.keys()) == {"b2b", "b2c"}
    assert set(b2b_prices.keys()) == {"b2b"}
    assert set(b2c_prices.keys()) == {"b2c"}
    assert b2c_prices["b2c"].amount == Decimal("2200000")


@pytest.mark.django_db
def test_cross_tenant_item_access_denied(seller):
    other = make_business(name="سنگ تست ب", owner_phone="09120000011", city="یزد")
    item = make_item(seller)
    assert get_business_lot(other, item.id) is None


@pytest.mark.django_db
def test_other_business_cannot_price_item(seller):
    other = make_business(name="کسب ب", owner_phone="09120000012", city="قم")
    item = make_item(seller)
    with pytest.raises(InventoryError):
        update_item(
            lot=item,
            membership=owner_membership(other),
            b2b_price={"mode": "fixed", "amount": Decimal("1")},
        )


# --- the four lifecycle axes are independent ---------------------------------


@pytest.mark.django_db
def test_editing_an_item_is_one_transaction(seller):
    """A rejected price must not leave the other fields already saved."""
    item = make_item(seller, b2b="1000", b2c="2000", grade="سوپر")

    with pytest.raises(InventoryError):
        update_item(
            lot=item,
            membership=owner_membership(seller),
            fields={"grade": "درجه دو"},
            # No amount with fixed mode: set_lot_price raises, and the grade
            # change must roll back with it.
            b2b_price={"mode": "fixed", "amount": None},
        )

    item.refresh_from_db()
    assert item.grade == "سوپر"


@pytest.mark.django_db
def test_unavailable_is_not_the_same_as_deleted_or_hidden(seller):
    item = make_item(seller)
    set_item_availability(lot=item, membership=owner_membership(seller), available=False)
    item.refresh_from_db()

    assert item.availability_status == InventoryLot.Availability.UNAVAILABLE
    assert item.is_visible is True, "marking unavailable must not silently unpublish"
    assert item.deleted_at is None
    # Still manageable by its owner: that is the whole point of not deleting it.
    assert item in lots_for_business(seller)


@pytest.mark.django_db
def test_owner_can_make_an_unavailable_item_available_again(seller):
    membership = owner_membership(seller)
    item = make_item(seller, availability_status=InventoryLot.Availability.UNAVAILABLE)

    set_item_availability(lot=item, membership=membership, available=True)
    item.refresh_from_db()
    assert item.availability_status == InventoryLot.Availability.AVAILABLE


# --- stock modes and freshness ------------------------------------------------


@pytest.mark.django_db
def test_exact_stock_shows_the_quantity(seller):
    item = make_item(seller, stock_mode=InventoryLot.StockMode.EXACT, available_sqm="650")
    view = stock_view(item)
    assert view.display == StockDisplay.EXACT
    assert view.quantity_sqm == Decimal("650.000")
    assert "650" in view.label


@pytest.mark.django_db
def test_unlimited_stock_shows_no_quantity(seller):
    item = make_item(seller, stock_mode=InventoryLot.StockMode.UNLIMITED, available_sqm="0")
    view = stock_view(item)
    assert view.display == StockDisplay.UNLIMITED
    assert view.label == "موجودی نامحدود"
    assert view.quantity_sqm is None


@pytest.mark.django_db
def test_inquiry_stock_never_asks_the_seller_to_reconfirm(seller):
    item = make_item(seller, stock_mode=InventoryLot.StockMode.INQUIRY)
    view = stock_view(item)
    assert view.display == StockDisplay.INQUIRY
    assert view.needs_confirmation is False
    assert needs_stock_confirmation(item) is False


@pytest.mark.django_db
def test_expired_exact_stock_degrades_to_inquiry_without_hiding_the_item(seller):
    item = make_item(seller, available_sqm="650", stock_valid_for_days=3)
    expire_stock(item)

    view = stock_view(item)
    assert view.display == StockDisplay.INQUIRY
    assert view.label == "استعلام موجودی"
    assert view.quantity_sqm is None, "a stale number must not be presented as current"
    assert view.needs_confirmation is True

    # The item itself is untouched: still visible, still available, still sellable.
    item.refresh_from_db()
    assert item.is_visible is True
    assert item.availability_status == InventoryLot.Availability.AVAILABLE
    assert item.available_sqm == Decimal("650.000"), "the stored figure is kept for the seller"


@pytest.mark.django_db
def test_confirming_stock_restores_the_trusted_display(seller):
    item = make_item(seller, available_sqm="650", stock_valid_for_days=3)
    expire_stock(item)
    assert stock_view(item).display == StockDisplay.INQUIRY

    confirm_item_stock(lot=item, membership=owner_membership(seller), available_sqm=Decimal("400"))
    item.refresh_from_db()

    view = stock_view(item)
    assert view.display == StockDisplay.EXACT
    assert view.quantity_sqm == Decimal("400.000")


@pytest.mark.django_db
def test_needs_confirmation_query_matches_the_property(seller):
    fresh = make_item(seller, lot_code="FRESH")
    stale = make_item(seller, lot_code="STALE", stock_valid_for_days=2)
    expire_stock(stale)
    inquiry = make_item(seller, lot_code="INQ", stock_mode=InventoryLot.StockMode.INQUIRY)

    flagged = set(filter_needs_stock_confirmation(lots_for_business(seller)).values_list("lot_code", flat=True))
    assert flagged == {"STALE"}
    assert needs_stock_confirmation(fresh) is False
    assert needs_stock_confirmation(inquiry) is False


@pytest.mark.django_db
def test_changing_the_quantity_restarts_the_validity_window(seller):
    item = make_item(seller, available_sqm="100", stock_valid_for_days=5)
    expire_stock(item)
    assert needs_stock_confirmation(item) is True

    update_item(
        lot=item,
        membership=owner_membership(seller),
        fields={"available_sqm": Decimal("120")},
    )
    item.refresh_from_db()
    assert needs_stock_confirmation(item) is False


@pytest.mark.django_db
def test_product_applications_are_a_controlled_vocabulary(seller):
    product = make_product(seller, applications=["floor", "stairs"])
    assert set(product.applications.values_list("code", flat=True)) == {"floor", "stairs"}


@pytest.mark.django_db
def test_new_items_have_a_stable_unique_share_token(seller):
    first = make_item(seller, lot_code="A")
    second = make_item(seller, lot_code="B")

    assert first.public_token and second.public_token
    assert first.public_token != second.public_token

    token = first.public_token
    first.grade = "سوپر"
    first.save()
    first.refresh_from_db()
    assert first.public_token == token, "the share link must survive an edit"


@pytest.mark.django_db
def test_unknown_user_cannot_be_used_as_a_membership(seller):
    """Sanity check that the builders provision through the real service."""
    stranger = make_user("09129999999")
    assert not stranger.memberships.exists()


# --- stock inquiry -------------------------------------------------------------


@pytest.mark.django_db
def test_a_buyer_can_ask_about_stale_stock_and_the_seller_can_answer(client, seller):
    """The «استعلام موجودی» loop: ask, notify, confirm, and the number returns."""
    from apps.inquiries.models import Inquiry
    from apps.notifications.models import Notification

    item = make_item(seller, lot_code="ASK-1", available_sqm="650", stock_valid_for_days=3, b2c="100")
    expire_stock(item)

    page = client.get(f"/p/{item.public_token}/").content.decode()
    assert "استعلام موجودی" in page

    response = client.post(
        f"/stock-inquiry/{item.id}/",
        {"name": "آقای رضایی", "phone": "09123334455"},
        follow=True,
    )
    assert response.status_code == 200

    inquiry = Inquiry.objects.get()
    assert inquiry.business_id == seller.id
    assert inquiry.items.get().item_id == item.id
    assert Notification.objects.filter(business=seller).exists()

    confirm_item_stock(lot=item, membership=owner_membership(seller), available_sqm=Decimal("300"))
    item.refresh_from_db()
    assert stock_view(item).display == StockDisplay.EXACT
