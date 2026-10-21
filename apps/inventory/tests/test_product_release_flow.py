from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.core.testing import make_business, owner_membership
from apps.inventory.forms import ProductItemForm, TierPriceForm
from apps.inventory.models import Application, InventoryLot, VocabularyTerm


def _login(client, business):
    client.force_login(owner_membership(business).user)
    session = client.session
    session["current_business_id"] = str(business.id)
    session.save()


def _payload():
    return {
        "stone": str(VocabularyTerm.objects.get(name="تراورتن").pk),
        "applications": [str(Application.objects.get(code="exterior-facade").pk)],
        "name_suffix": "آزمون قیمت",
        "width_cm": "40",
        "stock_valid_for_days": "7",
        "availability_status": InventoryLot.Availability.AVAILABLE,
        "b2b-valid_for_days": "7",
        "b2c-valid_for_days": "7",
    }


@pytest.mark.django_db
def test_product_requires_an_application():
    data = _payload()
    data.pop("applications")
    form = ProductItemForm(data)
    assert not form.is_valid()
    assert "applications" in form.errors


def test_special_sale_requires_checkbox_price_and_duration():
    assert "mode" not in TierPriceForm().fields
    missing = TierPriceForm({"valid_for_days": "7", "is_special": "on"})
    assert not missing.is_valid()
    assert {"amount", "special_amount", "special_valid_for_days"} <= set(missing.errors)

    ordinary = TierPriceForm({
        "valid_for_days": "7", "amount": "100000", "special_amount": "80000",
        "special_valid_for_days": "3",
    })
    assert ordinary.is_valid(), ordinary.errors
    assert ordinary.cleaned_data["special_amount"] is None
    assert ordinary.cleaned_data["special_valid_for_days"] is None

    excessive = TierPriceForm({
        "valid_for_days": "7", "amount": "100000", "is_special": "on",
        "special_amount": "110000", "special_valid_for_days": "3",
    })
    assert "special_amount" in excessive.errors

    too_long = TierPriceForm({
        "valid_for_days": "7", "amount": "100000", "is_special": "on",
        "special_amount": "90000", "special_valid_for_days": "10",
    })
    assert "special_valid_for_days" in too_long.errors


@pytest.mark.django_db
def test_product_price_follows_amount_and_special_checkbox(client):
    seller = make_business(name="سنگ قیمت آزمایشی", owner_phone="09120002901")
    _login(client, seller)
    data = _payload()
    data.update({
        "b2b-amount": "1200000", "b2b-is_special": "on",
        "b2b-special_amount": "990000", "b2b-special_valid_for_days": "3",
        "b2b-mode": "inquiry",  # obsolete client input must not override the amount
    })

    response = client.post(reverse("inventory:product_create"), data)

    assert response.status_code == 302
    lot = InventoryLot.objects.get(business=seller)
    b2b = lot.prices.get(tier__code="b2b")
    b2c = lot.prices.get(tier__code="b2c")
    assert b2b.mode == "fixed" and b2b.amount == 1200000
    assert b2b.special_amount == 990000
    assert 2.9 < (b2b.special_until - timezone.now()).total_seconds() / 86400 <= 3
    assert b2c.mode == "inquiry" and b2c.amount is None
    assert b2c.special_amount is None

    edit_url = reverse("inventory:lot_edit", kwargs={"lot_id": lot.id})
    edit_response = client.get(edit_url)
    assert 'name="b2b-is_special"' in edit_response.content.decode()
    assert edit_response.context["b2b_form"].initial["is_special"] is True
    assert edit_response.context["b2b_form"].initial["special_amount"] == b2b.special_amount

    original_until = b2b.special_until
    unchanged = _payload()
    unchanged.update({
        "b2b-amount": "1200000", "b2b-is_special": "on",
        "b2b-special_amount": "990000", "b2b-special_valid_for_days": "3",
    })
    assert client.post(edit_url, unchanged).status_code == 302
    b2b.refresh_from_db()
    assert b2b.special_until == original_until

    changed = _payload()
    changed.update({
        "b2b-amount": "1200000",
        "b2b-special_amount": "900000",  # stale value while checkbox is off
        "b2b-special_valid_for_days": "3",
    })
    assert client.post(edit_url, changed).status_code == 302
    b2b.refresh_from_db()
    assert b2b.mode == "fixed"
    assert b2b.special_amount is None and b2b.special_until is None


@pytest.mark.django_db
def test_create_page_exposes_required_applications_and_no_price_mode(client):
    seller = make_business(name="سنگ فرم آزمایشی", owner_phone="09120002902")
    _login(client, seller)

    body = client.get(reverse("inventory:product_create")).content.decode()

    assert "کاربرد محصول" in body
    assert "نوع قیمت" not in body
    assert 'name="b2b-is_special"' in body
    assert 'data-special-fields' in body
