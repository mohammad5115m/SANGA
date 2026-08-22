from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.catalog.selectors import resolve_catalog, selected_catalog_lots
from apps.catalog.services import (
    CatalogError,
    add_catalog_lots,
    create_custom_catalog,
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
