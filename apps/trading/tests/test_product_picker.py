from __future__ import annotations

import pytest
from django.urls import reverse

from apps.core.testing import make_business, owner_membership


def _login(client, business):
    membership = owner_membership(business)
    client.force_login(membership.user)
    session = client.session
    session["current_business_id"] = str(business.id)
    session.save()


@pytest.mark.django_db
def test_retired_proposal_picker_returns_no_products(client):
    buyer = make_business(name="خریدار انتخاب", owner_phone="09120002101")
    _login(client, buyer)

    response = client.get(
        reverse("trading:proposal_product_options"),
        {"direction": "buy", "counterparty": "not-a-uuid"},
    )

    assert response.status_code == 200
    assert response.json() == {"items": []}


@pytest.mark.django_db
def test_retired_proposal_form_redirects_to_invoice_creation(client):
    seller = make_business(name="فروشنده فرم", owner_phone="09120002104")
    _login(client, seller)

    response = client.get(reverse("trading:proposal_create"))

    assert response.status_code == 302
    assert response.url == reverse("invoicing:create")
