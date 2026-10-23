"""Consequential regressions across the shared create/edit journey."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.urls import reverse

from apps.businesses.models import Business, BusinessMembership
from apps.businesses.permissions import INVENTORY_CREATE, INVENTORY_EDIT, INVENTORY_VIEW
from apps.core.testing import make_business, make_item, make_user, owner_membership
from apps.inventory.forms import ProductItemForm, TierPriceForm
from apps.inventory.models import Application, InventoryLot, Product, VocabularyTerm
from apps.inventory.services import (
    InventoryError,
    create_draft_item,
    create_product,
    create_product_item,
    update_product_item,
)

from .test_media import PNG
from .test_product_release_flow import _login, _payload


@pytest.fixture
def world(db, client):
    business = make_business(name="بررسی ثبت محصول", owner_phone="09120003801")
    _login(client, business)
    return business, owner_membership(business)


def arguments(world):
    business, membership = world
    return {
        "business": business,
        "membership": membership,
        "product_fields": {"stone": VocabularyTerm.objects.get(name="تراورتن"), "name_suffix": "هم‌نام"},
        "item_fields": {"width_cm": Decimal("40"), "available_sqm": Decimal("12")},
        "applications": list(Application.objects.filter(code="floor")),
        "submission_id": uuid.uuid4(),
    }


@pytest.mark.parametrize("endpoint", ["inventory:product_create", "inventory:quick_add_start", "edit"])
def test_all_errors_are_reported_and_invalid_numeric_text_survives(client, world, endpoint):
    business, _ = world
    url = reverse(endpoint) if endpoint != "edit" else reverse(
        "inventory:lot_edit", args=[make_item(business).pk]
    )
    payload = _payload() | {"width_cm": "اشتباه", "b2b-amount": "-1", "b2c-amount": "-2"}
    response = client.post(url, payload)
    assert response.status_code == 200
    assert response.context["form"].errors.keys() == {"width_cm"}
    assert "amount" in response.context["b2b_form"].errors
    assert "amount" in response.context["b2c_form"].errors
    errors = response.context["form_errors"]
    assert {error["target"] for error in errors} == {"id_width_cm", "id_b2b-amount", "id_b2c-amount"}
    assert 'value="اشتباه"' in response.content.decode()
    assert InventoryLot.objects.filter(business=business).count() == (1 if endpoint == "edit" else 0)


@pytest.mark.parametrize("value", ["۱,۲", "1,,000", "12٬34", "-1", "NaN", "Infinity", "1e99999", "9" * 14])
def test_malformed_and_excessive_product_numbers_are_errors(world, value):
    form = ProductItemForm(_payload() | {"width_cm": value})
    assert not form.is_valid()
    assert "width_cm" in form.errors


def test_local_digits_grouping_and_thickness_round_trip(client, world):
    business, _ = world
    payload = _payload() | {
        "width_cm": "٤٠٫٥", "thickness_cm": "۱٫۷۵", "available_sqm": "۱٬۲۳۴٫۵۰۰",
        "b2b-amount": "۱,۲۰۰,۰۰۰", "b2c-amount": "٢,٠٠٠,٠٠٠", "min_sale_qty": "۰",
    }
    assert client.post(reverse("inventory:product_create"), payload).status_code == 302
    lot = InventoryLot.objects.get(business=business)
    assert lot.width_cm == Decimal("40.5")
    assert lot.thickness_mm == Decimal("17.5")
    assert lot.length_cm is None and lot.min_sale_qty == 0
    assert lot.available_sqm == Decimal("1234.5") and lot.is_stock_fresh
    assert lot.prices.get(tier__code="b2b").amount == 1200000
    response = client.get(reverse("inventory:lot_edit", args=[lot.pk]))
    assert response.context["form"].initial["thickness_cm"] == Decimal("1.75")


@pytest.mark.parametrize("quantity,availability,minimum,valid", [
    ("", "available", "20", True), ("0", "available", "", False),
    ("0", "unavailable", "0", True), ("10", "available", "11", False),
    ("10", "available", "10", True), ("-1", "unavailable", "", False),
])
def test_unknown_zero_and_minimum_order_remain_distinct(world, quantity, availability, minimum, valid):
    form = ProductItemForm(_payload() | {
        "available_sqm": quantity, "availability_status": availability, "min_sale_qty": minimum,
    })
    assert form.is_valid() is valid


@pytest.mark.parametrize("prefix", [None, "b2b"])
def test_disabled_promotion_ignores_even_malformed_stale_values(prefix):
    values = {"amount": "1000", "valid_for_days": "7", "special_amount": "خراب", "special_valid_for_days": "9999"}
    form = TierPriceForm({f"{prefix}-{key}" if prefix else key: value for key, value in values.items()}, prefix=prefix)
    assert form.is_valid(), form.errors
    assert form.cleaned_data["special_amount"] is None
    assert form.cleaned_data["special_valid_for_days"] is None


def test_retry_does_not_duplicate_but_new_token_creates_distinct_same_named_product(world):
    args = arguments(world)
    first = create_product_item(**args)
    assert create_product_item(**args) == first
    second = create_product_item(**(args | {"submission_id": uuid.uuid4()}))
    assert first.pk != second.pk and first.lot_code != second.lot_code
    assert first.product.commercial_name == second.product.commercial_name
    assert first.product.slug != second.product.slug
    assert Product.objects.filter(business=world[0]).count() == 2


@pytest.mark.parametrize("service", ["product", "draft", "item"])
def test_create_services_refuse_foreign_business_membership(world, service):
    args = arguments(world)
    foreign = make_business(name="کسب‌وکار دیگر", owner_phone="09120003802")
    with pytest.raises(InventoryError, match="کسب‌وکار"):
        if service == "product":
            create_product(business=foreign, membership=world[1], **args["product_fields"])
        elif service == "draft":
            create_draft_item(business=foreign, membership=world[1], product=make_item(foreign).product)
        else:
            create_product_item(**(args | {"business": foreign}))


@pytest.mark.parametrize("restriction", ["membership", "plan", "suspension"])
def test_replay_still_checks_current_authorization(world, restriction):
    args = arguments(world)
    create_product_item(**args)
    if restriction == "membership":
        world[1].status = BusinessMembership.Status.SUSPENDED
        world[1].save()
    elif restriction == "plan":
        world[0].plan = Business.Plan.BROWSE
        world[0].save()
    else:
        world[0].status = Business.Status.SUSPENDED
        world[0].save()
    with pytest.raises(InventoryError):
        create_product_item(**args)


def test_second_price_failure_rolls_back_product_lot_applications_and_first_price(world):
    args = arguments(world) | {"b2b_price": {"amount": Decimal("1000")}, "b2c_price": {"amount": Decimal("-1")}}
    with pytest.raises(InventoryError):
        create_product_item(**args)
    assert not Product.objects.filter(business=world[0]).exists()
    assert not InventoryLot.objects.filter(business=world[0]).exists()
    assert not Product.applications.through.objects.exists()
    assert create_product_item(**(args | {"b2c_price": {"amount": Decimal("2000")}})).prices.count() == 2


def test_slug_collision_retries_without_merging_products(world, monkeypatch):
    args = arguments(world)
    existing = create_product_item(**args).product
    original = Product.save
    calls = []

    def collide_once(self, *positional, **kwargs):
        calls.append(self)
        if len(calls) == 1:
            self.slug = existing.slug
            raise IntegrityError("simulated concurrent slug collision")
        return original(self, *positional, **kwargs)

    monkeypatch.setattr(Product, "save", collide_once)
    second = create_product_item(**(args | {"submission_id": uuid.uuid4()}))
    assert len(calls) == 2 and second.product_id != existing.pk


def test_long_generated_name_is_rejected_before_database_write(world):
    args = arguments(world)
    payload = _payload()
    stone = args["product_fields"]["stone"]
    stone.name = "س" * 100
    stone.save()
    payload.update({"stone": str(stone.pk), "name_suffix": "ن" * 160})
    form = ProductItemForm(payload)
    assert "name_suffix" in form.errors
    with pytest.raises(InventoryError, match="طولانی"):
        create_product_item(**(args | {"product_fields": {"stone": stone, "name_suffix": "ن" * 160}}))


def test_restricted_editor_ignores_crafted_private_price_publish_and_quantity_changes(client, world):
    business, _ = world
    lot = make_item(business, b2b="1000", private_address="آدرس مالک")
    lot.product.applications.set(Application.objects.filter(code="floor"))
    member = BusinessMembership.objects.create(
        business=business, user=make_user("09120003803"), role=BusinessMembership.Role.STAFF,
        permissions=[INVENTORY_VIEW, INVENTORY_EDIT, INVENTORY_CREATE],
    )
    client.force_login(member.user)
    payload = _payload() | {
        "name_suffix": "ویرایش مجاز", "available_sqm": "900", "stock_valid_for_days": "365",
        "availability_status": "unavailable", "private_address": "نباید ثبت شود", "b2b-amount": "900000",
    }
    assert client.post(reverse("inventory:lot_edit", args=[lot.pk]), payload).status_code == 302
    lot.refresh_from_db()
    assert lot.product.name_suffix == "ویرایش مجاز"
    assert lot.is_visible and lot.availability_status == "available" and lot.available_sqm == 100
    assert lot.stock_valid_for_days == 7 and lot.private_address == "آدرس مالک"
    assert lot.prices.get(tier__code="b2b").amount == 1000
    assert client.post(reverse("inventory:product_create"), payload | {"is_visible": "on"}).status_code == 302
    created = business.lots.exclude(pk=lot.pk).get()
    assert not created.is_visible and created.private_address == "" and not created.prices.exists()


def test_http_token_survives_errors_and_success_hands_off_to_media(client, world, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    business, _ = world
    url = reverse("inventory:quick_add_start")
    token = client.get(url).context["form"].initial["submission_id"]
    payload = _payload() | {"submission_id": str(token), "width_cm": "invalid"}
    invalid = client.post(url, payload)
    assert invalid.context["form"]["submission_id"].value() == str(token)
    payload["width_cm"] = "40"
    response = client.post(url, payload)
    assert client.post(url, payload).url == response.url
    lot = InventoryLot.objects.get(business=business)
    detail = client.get(response.url)
    media_url = reverse("inventory:lot_media", args=[lot.pk])
    assert media_url in detail.content.decode()
    result = client.post(media_url, {
        "action": "upload", "is_primary": "on", "images": [
            SimpleUploadedFile("invalid.jpg", b"bad"), SimpleUploadedFile("first.png", PNG),
            SimpleUploadedFile("second.png", PNG),
        ],
    })
    assert result.status_code == 200 and "images" in result.context["form"].errors
    assert lot.media.count() == 2 and lot.media.filter(is_primary=True).count() == 1
    with lot.media.get(is_primary=True).file.open("rb") as uploaded:
        assert uploaded.read() == PNG
    assert "first" in lot.media.get(is_primary=True).file.name


def test_configuration_and_inactive_choices_are_explained_and_rejected(client, world):
    data = _payload()
    VocabularyTerm.objects.update(is_active=False)
    Application.objects.update(is_active=False)
    response = client.get(reverse("inventory:product_create"))
    assert response.context["configuration_missing"]
    invalid = client.post(reverse("inventory:product_create"), data)
    assert {"stone", "applications"} <= set(invalid.context["form"].errors)
    assert not world[0].lots.exists()


@pytest.mark.parametrize("restriction", ["browse", "viewer", "bad_token"])
def test_crafted_creation_requests_cannot_bypass_restrictions(client, world, restriction):
    business, membership = world
    payload = _payload() | {"name_suffix": "داده حفظ‌شده", "is_visible": "on", "b2b-amount": "1000"}
    if restriction == "browse":
        business.plan = Business.Plan.BROWSE
        business.save()
    elif restriction == "viewer":
        membership.role = BusinessMembership.Role.VIEWER
        membership.permissions = [INVENTORY_VIEW]
        membership.save()
    else:
        payload["submission_id"] = "not-a-uuid"
    response = client.post(reverse("inventory:product_create"), payload)
    assert not business.lots.exists() and not business.products.exists()
    if restriction != "viewer":
        assert response.status_code == 200
        assert response.context["form_errors"][0]["target"] == "product-errors"
        assert 'value="داده حفظ‌شده"' in response.content.decode()
    else:
        assert response.status_code == 302


def test_failed_edit_rolls_back_identity_applications_stock_and_prices(world):
    lot = create_product_item(**(arguments(world) | {"b2b_price": {"amount": Decimal("1000")}}))
    original_name = lot.product.commercial_name
    original_applications = set(lot.product.applications.values_list("pk", flat=True))
    with pytest.raises(InventoryError):
        update_product_item(
            lot=lot, membership=world[1], product_fields={"name_suffix": "نباید ذخیره شود"},
            item_fields={"available_sqm": Decimal("10")},
            applications=list(Application.objects.filter(code="stairs")),
            b2b_price={"amount": Decimal("2000")}, b2c_price={"amount": Decimal("-1")},
        )
    lot.refresh_from_db()
    assert lot.product.commercial_name == original_name
    assert set(lot.product.applications.values_list("pk", flat=True)) == original_applications
    assert lot.available_sqm == 12 and lot.prices.get(tier__code="b2b").amount == 1000
    assert not lot.prices.filter(tier__code="b2c").exists()
