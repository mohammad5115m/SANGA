from __future__ import annotations

import pytest
from django.urls import reverse

from apps.core.testing import make_business, make_item, owner_membership


def _login(client, business):
    membership = owner_membership(business)
    client.force_login(membership.user)
    session = client.session
    session["current_business_id"] = str(business.id)
    session.save()


@pytest.mark.django_db
def test_buy_proposal_picker_only_returns_selected_sellers_public_inventory(client):
    buyer = make_business(name="خریدار انتخاب", owner_phone="09120002101")
    seller = make_business(name="فروشنده انتخاب", owner_phone="09120002102")
    other = make_business(name="فروشنده دیگر", owner_phone="09120002103")
    visible = make_item(seller, lot_code="TRADE-PICK")
    make_item(seller, lot_code="TRADE-HIDDEN", is_visible=False)
    make_item(other, lot_code="TRADE-OTHER")
    _login(client, buyer)

    response = client.get(
        reverse("trading:proposal_product_options"),
        {"direction": "buy", "counterparty": seller.id, "q": "TRADE"},
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [str(visible.id)]


@pytest.mark.django_db
def test_proposal_form_renders_lazy_picker_instead_of_all_product_options(client):
    seller = make_business(name="فروشنده فرم", owner_phone="09120002104")
    make_item(seller, lot_code="FORM-PICK")
    _login(client, seller)

    body = client.get(reverse("trading:proposal_create")).content.decode()

    assert "data-product-picker" in body
    assert "FORM-PICK" not in body


@pytest.mark.django_db
def test_invalid_counterparty_identifier_returns_an_empty_result(client):
    buyer = make_business(name="خریدار ورودی", owner_phone="09120002105")
    _login(client, buyer)

    response = client.get(
        reverse("trading:proposal_product_options"),
        {"direction": "buy", "counterparty": "not-a-uuid"},
    )

    assert response.status_code == 200
    assert response.json() == {"items": []}
