import pytest
from django.urls import reverse

from apps.businesses.models import BusinessMembership
from apps.businesses.permissions import INVENTORY_VIEW
from apps.core.testing import make_business, make_item, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def inventory(client):
    business = make_business(name="موجودی بررسی", owner_phone="09380003001")
    user = make_user("09380003002")
    member = BusinessMembership.objects.create(
        business=business, user=user, role="viewer", permissions=[INVENTORY_VIEW],
    )
    cheap = make_item(business, lot_code="REVIEW-1", b2c="1234567")
    costly = make_item(business, lot_code="REVIEW-2", b2c="9876543", is_visible=False)
    client.force_login(user)
    return business, member, cheap, costly


@pytest.mark.parametrize("query", [{}, {"price_min": "5000000"}, {"sort": "price_asc", "price_tier": "b2b"}])
def test_members_without_price_permission_cannot_infer_prices_from_filters(client, inventory, query):
    response = client.get(reverse("inventory:lot_list"), query)
    assert response.context["page"].total == 2
    assert response.context["price_bounds"] == {"minimum": None, "maximum": None}
    assert "price_min" not in response.context["filter_form"].fields
    assert "price_max" not in response.context["filter_form"].fields
    assert "price_tier" not in response.context["filter_form"].fields
    assert "1234567" not in response.content.decode()


def test_valid_inventory_state_survives_an_unrelated_invalid_filter(client, inventory):
    response = client.get(reverse("inventory:lot_list"), {"state": "hidden", "stone": "not-a-uuid"})
    assert response.context["filter_form"].errors
    assert [row["lot"].pk for row in response.context["rows"]] == [inventory[3].pk]


def test_zero_is_an_active_filter_and_can_be_cleared():
    from apps.inventory.forms import OwnerItemFilterForm

    form = OwnerItemFilterForm({"price_min": "0"})
    assert form.is_valid() and form.active_filter_count == 1


def test_inventory_prices_are_named_missing_when_no_price_has_been_saved(client):
    business = make_business(name="بدون قیمت", owner_phone="09380003003")
    lot = make_item(business)
    client.force_login(business.memberships.get(role="owner").user)
    page = client.get(reverse("inventory:lot_list"))
    assert lot.pk == page.context["rows"][0]["lot"].pk
    assert page.content.decode().count("ثبت نشده") >= 2


def test_catalog_destination_survives_filtering_and_invalid_destinations_do_not_crash(client):
    from apps.catalog.models import CustomCatalog

    business = make_business(name="مقصد کاتالوگ", owner_phone="09380003004")
    lot = make_item(business)
    catalog = CustomCatalog.objects.create(business=business, title="منتخب مشتری")
    client.force_login(business.memberships.get(role="owner").user)
    page = client.get(reverse("inventory:lot_list"), {"catalog": str(catalog.pk), "state": "available"})
    assert page.context["selected_catalog"] == catalog
    assert f'name="catalog" value="{catalog.pk}"' in page.content.decode()
    assert f"catalog={catalog.pk}" in page.context["filter_reset_url"]
    response = client.post(reverse("inventory:catalog_selection_start"), {
        "lot_ids": [str(lot.pk)], "catalog_id": "broken-uuid", "selection_scope": "selected",
    })
    assert response.status_code == 302
