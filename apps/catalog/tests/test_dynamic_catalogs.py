from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.catalog.selectors import resolve_catalog, selected_catalog_lots
from apps.catalog.services import (
    CatalogError,
    add_catalog_lots,
    create_custom_catalog,
    duplicate_catalog,
    move_catalog_lot,
    regenerate_catalog_token,
    remove_catalog_lot,
    set_catalog_lots,
    update_catalog,
)
from apps.core.testing import make_business, make_item, make_product, owner_membership
from apps.inventory.models import InventoryLot
from apps.pricing.services import ensure_default_tiers, set_lot_price


@pytest.fixture
def shop(db):
    ensure_default_tiers()
    seller = make_business(name="سنگ فروشنده", owner_phone="09221110001")
    first = make_item(
        seller,
        product=make_product(seller, commercial_name="تراورتن عباس‌آباد ۱"),
        lot_code="T-CAT001",
        b2c="2000000",
    )
    second = make_item(
        seller,
        product=make_product(seller, commercial_name="تراورتن عباس‌آباد ۲"),
        lot_code="T-CAT002",
        b2c="2200000",
    )
    other = make_item(
        seller,
        product=make_product(seller, commercial_name="مرمریت لاشتر", stone_type="مرمریت"),
        lot_code="M-CAT003",
        b2c="3000000",
    )
    return {
        "seller": seller,
        "membership": owner_membership(seller),
        "first": first,
        "second": second,
        "other": other,
    }


def _catalog(shop, ids):
    return create_custom_catalog(
        business=shop["seller"],
        membership=shop["membership"],
        title="انتخابی",
        lot_ids=ids,
    )


def _codes(catalog):
    return [item.lot_code for item in resolve_catalog(catalog)]


@pytest.mark.django_db
def test_catalog_contains_exactly_the_selected_items_in_order(shop):
    catalog = _catalog(shop, [shop["other"].id, shop["first"].id])
    assert _codes(catalog) == ["M-CAT003", "T-CAT001"]


@pytest.mark.django_db
def test_add_and_remove_catalog_items(shop):
    catalog = _catalog(shop, [shop["first"].id])
    add_catalog_lots(
        catalog=catalog,
        membership=shop["membership"],
        lot_ids=[shop["first"].id, shop["second"].id],
    )
    assert _codes(catalog) == ["T-CAT001", "T-CAT002"]
    remove_catalog_lot(
        catalog=catalog, membership=shop["membership"], lot_id=shop["first"].id
    )
    assert _codes(catalog) == ["T-CAT002"]


@pytest.mark.django_db
def test_cross_tenant_selection_is_rejected_atomically(shop):
    intruder = make_business(name="سنگ غریبه", owner_phone="09221110009")
    foreign = make_item(intruder, lot_code="T-FOR001")
    catalog = _catalog(shop, [shop["first"].id])
    with pytest.raises(CatalogError):
        set_catalog_lots(
            catalog=catalog,
            membership=shop["membership"],
            lot_ids=[shop["second"].id, foreign.id],
        )
    assert _codes(catalog) == ["T-CAT001"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "field,value",
    [
        ("is_visible", False),
        ("availability_status", InventoryLot.Availability.UNAVAILABLE),
        ("deleted_at", "now"),
        ("status", InventoryLot.Status.DRAFT),
    ],
)
def test_current_eligibility_always_wins(shop, field, value):
    catalog = _catalog(shop, [shop["first"].id])
    setattr(shop["first"], field, timezone.now() if value == "now" else value)
    shop["first"].save()
    assert _codes(catalog) == []


@pytest.mark.django_db
def test_item_returns_when_it_becomes_eligible_again(shop):
    catalog = _catalog(shop, [shop["first"].id])
    shop["first"].availability_status = InventoryLot.Availability.UNAVAILABLE
    shop["first"].save()
    assert _codes(catalog) == []
    shop["first"].availability_status = InventoryLot.Availability.AVAILABLE
    shop["first"].save()
    assert _codes(catalog) == ["T-CAT001"]


@pytest.mark.django_db
def test_hidden_selection_stays_visible_to_the_catalog_manager(shop):
    catalog = _catalog(shop, [shop["first"].id])
    shop["first"].is_visible = False
    shop["first"].save()

    assert _codes(catalog) == []
    assert [item.lot_code for item in selected_catalog_lots(catalog)] == ["T-CAT001"]


@pytest.mark.django_db
def test_public_catalog_never_renders_b2b_price(client, shop):
    set_lot_price(lot=shop["first"], tier_code="b2b", amount=Decimal("1111111"))
    catalog = _catalog(shop, [shop["first"].id])
    body = client.get(
        reverse("catalog:shared_catalog", kwargs={"share_token": catalog.share_token})
    ).content.decode().replace(",", "")
    assert "2000000" in body
    assert "1111111" not in body


@pytest.mark.django_db
def test_deactivated_catalog_link_is_not_public(client, shop):
    catalog = _catalog(shop, [shop["first"].id])
    update_catalog(catalog=catalog, membership=shop["membership"], is_active=False)
    url = reverse("catalog:shared_catalog", kwargs={"share_token": catalog.share_token})
    assert client.get(url).status_code == 404


@pytest.mark.django_db
def test_adding_products_preserves_existing_membership_notes(shop):
    catalog = _catalog(shop, [shop["first"].id])
    membership = catalog.items.get(lot=shop["first"])
    membership.note = "انتخاب اصلی مشتری"
    membership.save(update_fields=["note"])

    add_catalog_lots(
        catalog=catalog,
        membership=shop["membership"],
        lot_ids=[shop["second"].id],
    )

    assert catalog.items.get(lot=shop["first"]).note == "انتخاب اصلی مشتری"
    assert _codes(catalog) == ["T-CAT001", "T-CAT002"]


@pytest.mark.django_db
def test_customer_catalog_can_be_reordered_duplicated_and_revoked(client, shop):
    catalog = _catalog(shop, [shop["first"].id, shop["second"].id])
    second_membership = catalog.items.get(lot=shop["second"])
    assert move_catalog_lot(
        catalog=catalog,
        membership=shop["membership"],
        membership_id=second_membership.id,
        direction="up",
    )
    assert _codes(catalog) == ["T-CAT002", "T-CAT001"]

    copied = duplicate_catalog(catalog=catalog, membership=shop["membership"])
    assert copied.is_active is False
    assert copied.customer_name == ""
    assert copied.share_token != catalog.share_token
    assert _codes(copied) == ["T-CAT002", "T-CAT001"]

    old_url = reverse("catalog:shared_catalog", kwargs={"share_token": catalog.share_token})
    regenerate_catalog_token(catalog=catalog, membership=shop["membership"])
    new_url = reverse("catalog:shared_catalog", kwargs={"share_token": catalog.share_token})
    assert client.get(old_url).status_code == 404
    assert client.get(new_url).status_code == 200


@pytest.mark.django_db
def test_catalog_selection_replaces_storefront_context_and_source(client, shop):
    catalog = _catalog(shop, [shop["first"].id])
    toggle_url = reverse(
        "catalog:selection_toggle",
        kwargs={
            "storefront_token": shop["seller"].storefront_token,
            "item_id": shop["second"].id,
        },
    )
    client.post(toggle_url)

    catalog_toggle_url = reverse(
        "catalog:selection_toggle",
        kwargs={
            "storefront_token": shop["seller"].storefront_token,
            "item_id": shop["first"].id,
        },
    )
    client.post(catalog_toggle_url, {"catalog": catalog.share_token})
    session = client.session
    assert session["public_selection"]["context"] == f"catalog:{catalog.pk}"
    assert list(session["public_selection"]["items"]) == [str(shop["first"].pk)]
    assert session["public_selection_source"] == "custom_catalog"
