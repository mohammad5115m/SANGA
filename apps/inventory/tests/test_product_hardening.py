from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.core.testing import expire_price, make_business, make_item, make_user, owner_membership
from apps.inventory.filters import ItemFilterSpec
from apps.inventory.forms import ItemFilterForm, ProductItemForm
from apps.inventory.models import InventoryLot, VocabularyTerm
from apps.inventory.selectors import filter_owned_lots, lots_for_business
from apps.inventory.services import InventoryError, create_product_item, set_item_availability


def _login(client, business):
    membership = owner_membership(business)
    client.force_login(membership.user)
    session = client.session
    session["current_business_id"] = str(business.id)
    session.save()


@pytest.mark.django_db
def test_price_attention_filter_returns_missing_and_expired_prices_only():
    business = make_business(name="فروشنده", owner_phone="09120000091")
    fresh = make_item(business, lot_code="FRESH", b2c="100000")
    expired = make_item(business, lot_code="EXPIRED", b2c="200000")
    missing = make_item(business, lot_code="MISSING")
    expire_price(expired)

    result = set(filter_owned_lots(lots_for_business(business), state="needs_price"))

    assert result == {expired, missing}
    assert fresh not in result


@pytest.mark.django_db
def test_product_form_accepts_persian_digits_and_decimal_centimetres():
    stone = VocabularyTerm.objects.get(name="تراورتن")
    form = ProductItemForm(
        {
            "stone": stone.id,
            "name_suffix": "عباس‌آباد",
            "width_cm": "۴۰٫۵".replace("٫", "."),
            "thickness_cm": "۱.۷",
            "available_sqm": "۱۲.۵",
            "stock_valid_for_days": "۷",
            "min_sale_qty": "۱",
            "availability_status": InventoryLot.Availability.AVAILABLE,
        }
    )

    assert form.is_valid(), form.errors
    assert form.cleaned_data["length_cm"] is None
    assert form.cleaned_data["width_cm"] == Decimal("40.5")
    assert form.thickness_mm == Decimal("17")


@pytest.mark.django_db
def test_length_is_optional_for_free_stone_but_width_is_required():
    stone = VocabularyTerm.objects.get(name="تراورتن")
    missing_width = ProductItemForm(
        {
            "stone": stone.id,
            "length_cm": "120",
            "stock_valid_for_days": "7",
            "availability_status": InventoryLot.Availability.UNAVAILABLE,
        }
    )
    free_length = ProductItemForm(
        {
            "stone": stone.id,
            "width_cm": "40",
            "stock_valid_for_days": "7",
            "availability_status": InventoryLot.Availability.UNAVAILABLE,
        }
    )

    assert not missing_width.is_valid()
    assert set(missing_width.errors) == {"width_cm"}
    assert free_length.is_valid(), free_length.errors
    assert free_length.cleaned_data["length_cm"] is None


@pytest.mark.django_db
def test_search_treats_space_and_half_space_as_equivalent():
    seller = make_business(name="سنگ جست‌وجو", owner_phone="09120002001")
    item = make_item(seller, product=None, lot_code="SRCH-1")
    item.product.name_suffix = "عباس‌آباد موج‌دار"
    item.product.save(update_fields=["name_suffix"])

    results = ItemFilterSpec(q="عباس آباد").apply(lots_for_business(seller), audience="owner")

    assert item in results
    assert "عباس‌آباد" in item.product.commercial_name


def test_invalid_filter_keeps_other_valid_filters_and_reports_the_error():
    form = ItemFilterForm({"q": "تراورتن", "price_min": "نامعتبر"})

    spec = form.to_spec()

    assert spec.q == "تراورتن"
    assert "price_min" in form.errors


@pytest.mark.django_db
def test_product_creation_submission_token_is_idempotent():
    seller = make_business(name="سنگ تکرار", owner_phone="09120002002")
    membership = owner_membership(seller)
    stone = VocabularyTerm.objects.get(name="تراورتن")
    token = uuid.uuid4()
    arguments = {
        "business": seller,
        "membership": membership,
        "product_fields": {"stone": stone, "name_suffix": "یکتا"},
        "item_fields": {"available_sqm": Decimal("20"), "width_cm": Decimal("40")},
        "submission_id": token,
    }

    first = create_product_item(**arguments)
    second = create_product_item(**arguments)

    assert first == second
    assert InventoryLot.objects.filter(business=seller).count() == 1


@pytest.mark.django_db
def test_lot_rejects_a_product_from_another_business():
    seller = make_business(name="سنگ مالک", owner_phone="09120002003")
    other = make_business(name="سنگ دیگر", owner_phone="09120002004")
    foreign_product = make_item(other, lot_code="FOREIGN-1").product

    with pytest.raises(ValidationError) as exc_info:
        InventoryLot.objects.create(
            business=seller,
            product=foreign_product,
            lot_code="BAD-OWNER",
        )
    assert "یک کسب‌وکار" in exc_info.value.messages[0]


@pytest.mark.django_db
def test_availability_change_requires_publish_capability():
    from apps.businesses.models import BusinessMembership
    from apps.businesses.permissions import INVENTORY_EDIT

    seller = make_business(name="سنگ مجوز", owner_phone="09120002005")
    item = make_item(seller, lot_code="PERM-1")
    member = BusinessMembership.objects.create(
        user=make_user("09120002006"),
        business=seller,
        role=BusinessMembership.Role.STAFF,
        permissions=[INVENTORY_EDIT],
    )

    with pytest.raises(InventoryError, match="دسترسی"):
        set_item_availability(lot=item, membership=member, available=False)


@pytest.mark.django_db
def test_product_options_are_tenant_scoped(client):
    seller = make_business(name="سنگ انتخاب", owner_phone="09120002007")
    other = make_business(name="سنگ بیرونی", owner_phone="09120002008")
    own = make_item(seller, lot_code="PICK-OWN")
    make_item(other, lot_code="PICK-OTHER")
    _login(client, seller)

    response = client.get(reverse("inventory:product_options"), {"q": "PICK"})
    payload = response.json()

    assert response.status_code == 200
    assert [item["id"] for item in payload["items"]] == [str(own.id)]
