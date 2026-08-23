from __future__ import annotations

import pytest
from django.urls import reverse

from apps.businesses.models import BusinessMembership
from apps.core.testing import make_business, make_item, make_user, owner_membership
from apps.inventory.forms import ProductItemForm
from apps.inventory.models import InventoryLot, Product, VocabularyTerm
from apps.inventory.services import InventoryError, update_item


def _login(client, membership: BusinessMembership) -> None:
    client.force_login(membership.user)
    session = client.session
    session["current_business_id"] = str(membership.business_id)
    session.save()


def _manager(business, phone: str = "09124440009") -> BusinessMembership:
    return BusinessMembership.objects.create(
        user=make_user(phone),
        business=business,
        role=BusinessMembership.Role.MANAGER,
    )


@pytest.mark.django_db
def test_product_form_uses_plain_stone_labels_and_the_simplified_layout(client):
    business = make_business(name="فرم ساده محصول", owner_phone="09124440001")
    _login(client, owner_membership(business))

    response = client.get(reverse("inventory:product_create"))
    body = response.content.decode()

    assert response.status_code == 200
    assert ">تراورتن</option>" in body
    assert "نوع سنگ: تراورتن" not in body
    assert "application-options" in body
    assert "اختیاری · چندانتخابی" in body
    assert 'name="pattern"' not in body
    assert 'name="dimension_mode"' not in body
    assert "خالی‌بودن طول به‌معنای طول آزاد است" in body


@pytest.mark.django_db
def test_private_fields_only_exist_on_an_owner_form(client):
    business = make_business(name="حریم فرم محصول", owner_phone="09124440002")
    owner = owner_membership(business)
    manager = _manager(business)

    _login(client, owner)
    owner_body = client.get(reverse("inventory:product_create")).content.decode()
    assert 'name="description_private"' in owner_body
    assert 'name="private_address"' in owner_body
    assert "فقط مالک" in owner_body

    _login(client, manager)
    manager_body = client.get(reverse("inventory:product_create")).content.decode()
    assert 'name="description_private"' not in manager_body
    assert 'name="private_address"' not in manager_body

    form = ProductItemForm(include_private=False)
    assert "description_private" not in form.fields
    assert "private_address" not in form.fields


@pytest.mark.django_db
def test_server_rejects_private_updates_from_a_non_owner():
    business = make_business(name="کنترل خصوصی محصول", owner_phone="09124440003")
    item = make_item(business, lot_code="PRIVATE-1")
    manager = _manager(business, "09124440004")

    with pytest.raises(InventoryError, match="فقط مالک"):
        update_item(
            lot=item,
            membership=manager,
            fields={"private_address": "آدرس نباید ثبت شود"},
        )

    item.refresh_from_db()
    assert item.private_address == ""


@pytest.mark.django_db
def test_each_audience_only_sees_its_approved_descriptions(client):
    seller = make_business(name="توضیحات تفکیک‌شده", owner_phone="09124440005")
    buyer = make_business(name="خریدار همکار", owner_phone="09124440006")
    product = Product.objects.create(
        business=seller,
        stone=VocabularyTerm.objects.get(name="تراورتن"),
        name_suffix="حریم تست",
        description_public="متن مخصوص تمام خریداران",
        description_colleague="متن افزوده مخصوص همکار",
    )
    item = make_item(
        seller,
        product=product,
        lot_code="AUDIENCE-1",
        b2b="1000000",
        b2c="1500000",
        description_private="یادداشت کاملاً شخصی مالک",
        private_address="آدرس خصوصی انبار محصول",
    )

    public_body = client.get(f"/p/{item.public_token}/").content.decode()
    assert "متن مخصوص تمام خریداران" in public_body
    assert "متن افزوده مخصوص همکار" not in public_body
    assert "یادداشت کاملاً شخصی مالک" not in public_body
    assert "آدرس خصوصی انبار محصول" not in public_body

    _login(client, owner_membership(buyer))
    colleague_body = client.get(
        reverse("marketplace:lot_detail", kwargs={"lot_id": item.id})
    ).content.decode()
    assert "متن مخصوص تمام خریداران" in colleague_body
    assert "متن افزوده مخصوص همکار" in colleague_body
    assert "یادداشت کاملاً شخصی مالک" not in colleague_body
    assert "آدرس خصوصی انبار محصول" not in colleague_body

    _login(client, owner_membership(seller))
    owner_body = client.get(
        reverse("inventory:lot_detail", kwargs={"lot_id": item.id})
    ).content.decode()
    assert "متن مخصوص تمام خریداران" in owner_body
    assert "متن افزوده مخصوص همکار" in owner_body
    assert "یادداشت کاملاً شخصی مالک" in owner_body
    assert "آدرس خصوصی انبار محصول" in owner_body

    manager = _manager(seller, "09124440007")
    _login(client, manager)
    manager_body = client.get(
        reverse("inventory:lot_detail", kwargs={"lot_id": item.id})
    ).content.decode()
    assert "متن مخصوص تمام خریداران" in manager_body
    assert "متن افزوده مخصوص همکار" in manager_body
    assert "یادداشت کاملاً شخصی مالک" not in manager_body
    assert "آدرس خصوصی انبار محصول" not in manager_body


def test_pattern_field_is_removed_from_the_current_product_model():
    field_names = {field.name for field in Product._meta.get_fields()}
    assert "pattern" not in field_names
    assert {"description_public", "description_colleague"} <= field_names
    assert {"description_private", "private_address"} <= {
        field.name for field in InventoryLot._meta.get_fields()
    }
