from urllib.parse import urlencode

import pytest
from django.urls import reverse

from apps.catalog.models import CustomCatalog
from apps.core.testing import make_business, make_item, make_product, owner_membership


@pytest.fixture
def stocked_shop(db):
    seller = make_business(name="سنگ انتخاب", owner_phone="09361110001")
    first = make_item(seller, lot_code="T-SEL101")
    second = make_item(seller, lot_code="T-SEL102")
    marble = make_item(
        seller,
        product=make_product(seller, commercial_name="مرمریت انتخاب", stone_type="مرمریت"),
        lot_code="M-SEL103",
    )
    return seller, first, second, marble


def _login(client, business):
    membership = owner_membership(business)
    client.force_login(membership.user)
    session = client.session
    session["current_business_id"] = str(business.pk)
    session.save()


def _finish_catalog(client, response, *, title="انتخاب تازه"):
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("catalog_manage:create"))
    return client.post(response["Location"], {"title": title, "is_active": "on"})


@pytest.mark.django_db
def test_selected_items_create_an_explicit_catalog_in_checked_order(client, stocked_shop):
    seller, first, _second, marble = stocked_shop
    _login(client, seller)
    response = client.post(
        reverse("inventory:catalog_selection_start"),
        {"selection_scope": "selected", "lot_ids": [str(marble.pk), str(first.pk)]},
    )

    created = _finish_catalog(client, response)

    assert created.status_code == 302
    catalog = CustomCatalog.objects.get(title="انتخاب تازه")
    assert list(catalog.items.order_by("sort_order").values_list("lot_id", flat=True)) == [
        marble.pk,
        first.pk,
    ]


@pytest.mark.django_db
def test_all_filter_results_are_selected_across_the_whole_queryset(client, stocked_shop):
    seller, first, second, _marble = stocked_shop
    _login(client, seller)
    response = client.post(
        reverse("inventory:catalog_selection_start"),
        {
            "selection_scope": "filter",
            "filter_query": urlencode({"stone": first.product.stone_id}),
        },
    )

    created = _finish_catalog(client, response, title="همه تراورتن‌ها")

    assert created.status_code == 302
    selected = set(
        CustomCatalog.objects.get(title="همه تراورتن‌ها").items.values_list("lot_id", flat=True)
    )
    assert selected == {first.pk, second.pk}


@pytest.mark.django_db
def test_selected_ids_from_another_business_are_rejected(client, stocked_shop):
    seller, first, _second, _marble = stocked_shop
    intruder = make_business(name="سنگ دیگر", owner_phone="09361110002")
    foreign = make_item(intruder, lot_code="T-SEL999")
    _login(client, seller)

    response = client.post(
        reverse("inventory:catalog_selection_start"),
        {"selection_scope": "selected", "lot_ids": [str(first.pk), str(foreign.pk)]},
    )

    assert response.status_code == 302
    assert response["Location"] == reverse("inventory:lot_list")
    assert CustomCatalog.objects.count() == 0
