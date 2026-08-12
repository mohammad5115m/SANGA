from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.businesses.models import Business, BusinessMembership
from apps.businesses.services import add_warehouse, create_business_for_owner
from apps.inventory.models import InventoryLot, Product
from apps.marketplace.selectors import get_marketplace_lot, marketplace_lots_for
from apps.marketplace.services import b2b_price_context
from apps.pricing.services import ensure_default_tiers, set_lot_prices

User = get_user_model()


@pytest.fixture
def two_businesses(db):
    ensure_default_tiers()
    owner_a = User.objects.create_user(phone="09126660001", full_name="تأمین")
    owner_b = User.objects.create_user(phone="09126660002", full_name="خریدار")
    supplier = create_business_for_owner(owner=owner_a, name="تأمین‌کننده آلفا", city="محلات")
    buyer = create_business_for_owner(owner=owner_b, name="خریدار بتا", city="تهران")
    wh = add_warehouse(business=supplier, name="انبار", is_default=True)
    membership_a = BusinessMembership.objects.get(user=owner_a, business=supplier)
    membership_b = BusinessMembership.objects.get(user=owner_b, business=buyer)
    product = Product.objects.create(business=supplier, commercial_name="مرمریت شبکه", stone_type="مرمریت")

    def make_lot(code, visibility, b2b="1000000", b2c="2000000"):
        lot = InventoryLot.objects.create(
            business=supplier,
            product=product,
            warehouse=wh,
            lot_code=code,
            status=InventoryLot.Status.AVAILABLE,
            visibility=visibility,
            available_sqm=Decimal("50"),
            original_sqm=Decimal("50"),
            inventory_confirmed_at=timezone.now(),
        )
        set_lot_prices(lot=lot, b2b_amount=Decimal(b2b), b2c_amount=Decimal(b2c))
        return lot

    return {
        "owner_a": owner_a,
        "owner_b": owner_b,
        "supplier": supplier,
        "buyer": buyer,
        "membership_a": membership_a,
        "membership_b": membership_b,
        "colleagues_lot": make_lot("NET-1", InventoryLot.Visibility.COLLEAGUES, "1500000", "2500000"),
        "private_lot": make_lot("HID-1", InventoryLot.Visibility.PRIVATE, "1700000", "2700000"),
        "public_lot": make_lot("PUB-1", InventoryLot.Visibility.PUBLIC, "1800000", "2800000"),
    }


def _login_as_buyer(client, ctx) -> None:
    client.force_login(ctx["owner_b"])
    session = client.session
    session["current_business_id"] = str(ctx["buyer"].id)
    session.save()


@pytest.mark.django_db
def test_b2b_context_excludes_b2c(two_businesses):
    ctx = b2b_price_context(two_businesses["colleagues_lot"])
    assert ctx["amount"] == Decimal("1500000")
    assert "b2c" not in ctx
    assert "2500000" not in str(ctx)


@pytest.mark.django_db
def test_any_business_sees_colleagues_and_public_lots(two_businesses):
    """No partnership of any kind exists between these two businesses."""
    codes = set(marketplace_lots_for(two_businesses["buyer"]).values_list("lot_code", flat=True))
    assert codes == {"NET-1", "PUB-1"}


@pytest.mark.django_db
def test_colleagues_lot_carries_b2b_price_only(two_businesses):
    lot = get_marketplace_lot(two_businesses["buyer"], two_businesses["colleagues_lot"].id)
    assert lot is not None
    assert {price.tier.code for price in lot.prices.all()} == {"b2b"}
    assert b2b_price_context(lot)["amount"] == Decimal("1500000")


@pytest.mark.django_db
def test_colleagues_lot_detail_page_opens_for_any_business(client, two_businesses):
    _login_as_buyer(client, two_businesses)
    lot = two_businesses["colleagues_lot"]

    response = client.get(reverse("marketplace:lot_detail", kwargs={"lot_id": lot.id}))
    assert response.status_code == 200
    content = response.content.decode("utf-8").replace(",", "")
    assert "NET-1" in content
    assert "1500000" in content
    assert "2500000" not in content


@pytest.mark.django_db
def test_private_lot_never_visible(two_businesses):
    private_lot = two_businesses["private_lot"]
    assert get_marketplace_lot(two_businesses["buyer"], private_lot.id) is None
    # The owner reaches it through its own inventory, not the marketplace.
    assert get_marketplace_lot(two_businesses["supplier"], private_lot.id) is None


@pytest.mark.django_db
def test_private_lot_detail_is_refused(client, two_businesses):
    _login_as_buyer(client, two_businesses)
    private_lot = two_businesses["private_lot"]

    response = client.get(reverse("marketplace:lot_detail", kwargs={"lot_id": private_lot.id}), follow=True)
    assert response.redirect_chain
    content = response.content.decode("utf-8").replace(",", "")
    assert "HID-1" not in content
    assert "1700000" not in content


@pytest.mark.django_db
def test_marketplace_excludes_viewers_own_lots(two_businesses):
    codes = set(marketplace_lots_for(two_businesses["supplier"]).values_list("lot_code", flat=True))
    assert codes == set()


@pytest.mark.django_db
def test_marketplace_gate_costs_no_query_per_lot(two_businesses, django_assert_num_queries):
    codes = marketplace_lots_for(two_businesses["buyer"]).values_list("lot_code", flat=True)
    with django_assert_num_queries(1):
        assert sorted(codes) == ["NET-1", "PUB-1"]


@pytest.mark.django_db
def test_a_suspended_viewer_sees_an_empty_marketplace(two_businesses):
    buyer = two_businesses["buyer"]
    buyer.status = Business.Status.SUSPENDED
    buyer.save(update_fields=["status"])

    assert list(marketplace_lots_for(buyer)) == []
    assert get_marketplace_lot(buyer, two_businesses["colleagues_lot"].id) is None


@pytest.mark.django_db
def test_a_suspended_owners_lots_leave_the_marketplace(two_businesses):
    supplier = two_businesses["supplier"]
    supplier.status = Business.Status.SUSPENDED
    supplier.save(update_fields=["status"])

    buyer = two_businesses["buyer"]
    assert list(marketplace_lots_for(buyer)) == []
    # Nor by UUID: the B2B price of a suspended colleague stays out of reach.
    assert get_marketplace_lot(buyer, two_businesses["public_lot"].id) is None


@pytest.mark.django_db
def test_an_active_business_is_unaffected_by_the_gate(two_businesses):
    codes = set(marketplace_lots_for(two_businesses["buyer"]).values_list("lot_code", flat=True))
    assert codes == {"NET-1", "PUB-1"}


@pytest.mark.django_db
def test_the_suspended_lot_detail_page_is_refused(client, two_businesses):
    _login_as_buyer(client, two_businesses)
    supplier = two_businesses["supplier"]
    supplier.status = Business.Status.SUSPENDED
    supplier.save(update_fields=["status"])

    response = client.get(
        reverse("marketplace:lot_detail", kwargs={"lot_id": two_businesses["public_lot"].id}),
        follow=True,
    )
    assert response.redirect_chain
    content = response.content.decode("utf-8").replace(",", "")
    assert "PUB-1" not in content
    assert "1800000" not in content


@pytest.mark.django_db
def test_marketplace_page_shows_b2b_not_b2c(client, two_businesses):
    _login_as_buyer(client, two_businesses)

    response = client.get(reverse("marketplace:home"))
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "1500000" in content.replace(",", "")
    assert "2500000" not in content.replace(",", "")
    assert "HID-1" not in content


@pytest.mark.django_db
def test_marketplace_hides_supplier_visibility_choice(client, two_businesses):
    _login_as_buyer(client, two_businesses)

    response = client.get(reverse("marketplace:home"))
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    # The cards are rendered...
    assert "مرمریت شبکه" in content
    # ...but whether the supplier published a lot publicly or to colleagues only
    # is an internal distribution decision and must not reach another business.
    # (COLLEAGUES.label is not asserted: «همکاران» is also the page's own title.)
    assert InventoryLot.Visibility.PUBLIC.label not in content
    assert InventoryLot.Visibility.PRIVATE.label not in content
    assert InventoryLot.Visibility.COLLEAGUES.value not in content
    assert InventoryLot.Visibility.PUBLIC.value not in content


@pytest.mark.django_db
def test_anonymous_cannot_open_marketplace(client):
    response = client.get(reverse("marketplace:home"))
    assert response.status_code in {302, 301}
