from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.core.testing import make_business, make_item, owner_membership
from apps.invoicing.forms import InvoiceLineForm
from apps.invoicing.services import InvoiceError, create_manual_invoice


@pytest.mark.django_db
def test_manual_invoice_service_rejects_another_business_product():
    seller = make_business(name="فاکتور مالک", owner_phone="09120002201")
    other = make_business(name="فاکتور دیگر", owner_phone="09120002202")
    foreign = make_item(other, lot_code="INV-FOREIGN")

    with pytest.raises(InvoiceError, match="متعلق"):
        create_manual_invoice(
            business=seller,
            membership=owner_membership(seller),
            customer_name="مشتری",
            lines=[
                {
                    "item": foreign,
                    "quantity": Decimal("1"),
                    "unit_price": Decimal("1000"),
                }
            ],
        )


@pytest.mark.django_db
def test_invoice_form_uses_lazy_product_picker(client):
    seller = make_business(name="فاکتور فرم", owner_phone="09120002203")
    make_item(seller, lot_code="INV-PICK")
    membership = owner_membership(seller)
    client.force_login(membership.user)
    session = client.session
    session["current_business_id"] = str(seller.id)
    session.save()

    body = client.get(reverse("invoicing:create")).content.decode()

    assert "data-product-picker" in body
    assert "INV-PICK" not in body


def test_invalid_product_identifier_becomes_a_field_error():
    form = InvoiceLineForm(
        {
            "item": "not-a-uuid",
            "quantity": "1",
            "unit_price": "1000",
        }
    )

    assert not form.is_valid()
    assert "item" in form.errors
