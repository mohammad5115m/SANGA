"""Manual, rule-based and hybrid catalogs.

The defining property: a catalog is **live**. A new matching product appears
without anyone editing the catalog, and a withdrawn one disappears. That is the
deliberate opposite of an invoice, which is frozen.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import CustomCatalog, CustomCatalogItem
from apps.catalog.selectors import resolve_catalog
from apps.catalog.services import (
    CatalogError,
    create_custom_catalog,
    set_catalog_exclusions,
    set_catalog_lots,
    update_catalog,
)
from apps.core.testing import make_business, make_item, make_product, owner_membership
from apps.inventory.models import InventoryLot
from apps.pricing.services import ensure_default_tiers


@pytest.fixture
def shop(db):
    ensure_default_tiers()
    seller = make_business(name="سنگ فروشنده", owner_phone="09221110001")
    abbas_one = make_item(
        seller,
        product=make_product(seller, commercial_name="تراورتن عباس‌آباد ۱", quarry_region="عباس‌آباد"),
        lot_code="AB-1",
        b2c="2000000",
    )
    abbas_two = make_item(
        seller,
        product=make_product(seller, commercial_name="تراورتن عباس‌آباد ۲", quarry_region="عباس‌آباد"),
        lot_code="AB-2",
        b2c="2200000",
    )
    other_quarry = make_item(
        seller,
        product=make_product(seller, commercial_name="مرمریت نی‌ریز", quarry_region="نی‌ریز"),
        lot_code="NR-1",
        b2c="3000000",
    )
    return {
        "seller": seller,
        "membership": owner_membership(seller),
        "abbas_one": abbas_one,
        "abbas_two": abbas_two,
        "other": other_quarry,
    }


def _rule_catalog(shop, rules=None, **kwargs) -> CustomCatalog:
    params = {
        "business": shop["seller"],
        "membership": shop["membership"],
        "title": "همه عباس‌آباد",
        "mode": CustomCatalog.Mode.RULE,
        "rules": rules if rules is not None else {"quarry_region": "عباس‌آباد"},
    }
    params.update(kwargs)
    return create_custom_catalog(**params)


def _codes(catalog) -> set[str]:
    return {item.lot_code for item in resolve_catalog(catalog)}


# --- manual catalogs ------------------------------------------------------------


@pytest.mark.django_db
def test_a_manual_catalog_shows_exactly_what_was_picked(shop):
    catalog = create_custom_catalog(
        business=shop["seller"],
        membership=shop["membership"],
        title="انتخابی",
        lot_ids=[shop["abbas_one"].id, shop["other"].id],
    )
    assert _codes(catalog) == {"AB-1", "NR-1"}


@pytest.mark.django_db
def test_a_manual_catalog_preserves_the_sellers_order(shop):
    catalog = create_custom_catalog(
        business=shop["seller"],
        membership=shop["membership"],
        title="انتخابی",
        lot_ids=[shop["other"].id, shop["abbas_one"].id],
    )
    assert [item.lot_code for item in resolve_catalog(catalog)] == ["NR-1", "AB-1"]


# --- rule-based catalogs --------------------------------------------------------


@pytest.mark.django_db
def test_a_rule_catalog_selects_everything_matching(shop):
    assert _codes(_rule_catalog(shop)) == {"AB-1", "AB-2"}


@pytest.mark.django_db
def test_a_new_matching_product_joins_the_catalog_by_itself(shop):
    """The whole point of a rule catalog: nobody edits it."""
    catalog = _rule_catalog(shop)
    assert _codes(catalog) == {"AB-1", "AB-2"}

    make_item(
        shop["seller"],
        product=make_product(shop["seller"], commercial_name="تراورتن عباس‌آباد ۳", quarry_region="عباس‌آباد"),
        lot_code="AB-3",
        b2c="2400000",
    )
    assert _codes(catalog) == {"AB-1", "AB-2", "AB-3"}


@pytest.mark.django_db
def test_a_product_that_stops_matching_leaves_the_catalog(shop):
    catalog = _rule_catalog(shop)
    product = shop["abbas_two"].product
    product.quarry_region = "جای دیگر"
    product.save(update_fields=["quarry_region"])
    assert _codes(catalog) == {"AB-1"}


@pytest.mark.django_db
def test_a_rule_catalog_needs_at_least_one_filter(shop):
    with pytest.raises(CatalogError):
        _rule_catalog(shop, rules={})


@pytest.mark.django_db
def test_rules_are_stored_in_the_shared_filter_vocabulary(shop):
    """A rule catalog is literally a saved search, not a second language."""
    catalog = _rule_catalog(shop, rules={"quarry_region": "عباس‌آباد", "unknown_key": "x"})
    assert catalog.rules == {"quarry_region": "عباس‌آباد"}


@pytest.mark.django_db
def test_a_price_rule_uses_the_public_tier(shop):
    catalog = _rule_catalog(
        shop,
        rules={"price_min": "2900000", "price_max": "3100000"},
        title="گران‌ها",
    )
    assert _codes(catalog) == {"NR-1"}


# --- hybrid: rules plus manual overrides ----------------------------------------


@pytest.mark.django_db
def test_a_hybrid_catalog_adds_a_manual_include_to_the_rule(shop):
    catalog = _rule_catalog(shop, mode=CustomCatalog.Mode.HYBRID)
    set_catalog_lots(catalog=catalog, membership=shop["membership"], lot_ids=[shop["other"].id])
    assert _codes(catalog) == {"AB-1", "AB-2", "NR-1"}


@pytest.mark.django_db
def test_a_manual_exclusion_removes_one_product_without_changing_the_rule(shop):
    catalog = _rule_catalog(shop)
    set_catalog_exclusions(
        catalog=catalog,
        membership=shop["membership"],
        lot_ids=[shop["abbas_two"].id],
    )
    assert _codes(catalog) == {"AB-1"}
    assert catalog.rules == {"quarry_region": "عباس‌آباد"}


@pytest.mark.django_db
def test_excluding_a_manually_included_product_replaces_the_include(shop):
    """The two instructions contradict; the newer one wins rather than both being stored."""
    catalog = _rule_catalog(shop, mode=CustomCatalog.Mode.HYBRID)
    set_catalog_lots(catalog=catalog, membership=shop["membership"], lot_ids=[shop["other"].id])
    assert "NR-1" in _codes(catalog)

    set_catalog_exclusions(catalog=catalog, membership=shop["membership"], lot_ids=[shop["other"].id])
    assert "NR-1" not in _codes(catalog)
    assert catalog.items.filter(lot=shop["other"]).count() == 1


@pytest.mark.django_db
def test_including_a_previously_excluded_product_replaces_the_exclusion(shop):
    catalog = _rule_catalog(shop, mode=CustomCatalog.Mode.HYBRID)
    set_catalog_exclusions(catalog=catalog, membership=shop["membership"], lot_ids=[shop["abbas_one"].id])
    assert "AB-1" not in _codes(catalog)

    set_catalog_lots(catalog=catalog, membership=shop["membership"], lot_ids=[shop["abbas_one"].id])
    assert "AB-1" in _codes(catalog)


@pytest.mark.django_db
def test_editing_the_manual_selection_keeps_exclusions(shop):
    catalog = _rule_catalog(shop, mode=CustomCatalog.Mode.HYBRID)
    set_catalog_exclusions(catalog=catalog, membership=shop["membership"], lot_ids=[shop["abbas_two"].id])
    set_catalog_lots(catalog=catalog, membership=shop["membership"], lot_ids=[shop["other"].id])

    assert catalog.items.filter(inclusion=CustomCatalogItem.Inclusion.EXCLUDE).count() == 1
    assert _codes(catalog) == {"AB-1", "NR-1"}


# --- eligibility always wins ----------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda i: setattr(i, "is_visible", False), id="hidden"),
        pytest.param(
            lambda i: setattr(i, "availability_status", InventoryLot.Availability.UNAVAILABLE),
            id="unavailable",
        ),
        pytest.param(lambda i: setattr(i, "deleted_at", timezone.now()), id="deleted"),
        pytest.param(lambda i: setattr(i, "status", InventoryLot.Status.DRAFT), id="draft"),
    ],
)
def test_an_ineligible_product_leaves_every_catalog_mode(shop, mutate):
    rule = _rule_catalog(shop)
    manual = create_custom_catalog(
        business=shop["seller"],
        membership=shop["membership"],
        title="دستی",
        lot_ids=[shop["abbas_one"].id],
    )

    mutate(shop["abbas_one"])
    shop["abbas_one"].save()

    assert "AB-1" not in _codes(rule)
    assert "AB-1" not in _codes(manual)


@pytest.mark.django_db
def test_an_available_again_product_returns_automatically(shop):
    """No re-curation: availability is re-evaluated on every read."""
    catalog = _rule_catalog(shop)
    shop["abbas_one"].availability_status = InventoryLot.Availability.UNAVAILABLE
    shop["abbas_one"].save()
    assert "AB-1" not in _codes(catalog)

    shop["abbas_one"].availability_status = InventoryLot.Availability.AVAILABLE
    shop["abbas_one"].save()
    assert "AB-1" in _codes(catalog)


@pytest.mark.django_db
def test_another_businesss_product_never_matches_a_rule(shop):
    intruder = make_business(name="سنگ غریبه", owner_phone="09221110009")
    make_item(
        intruder,
        product=make_product(intruder, commercial_name="تراورتن عباس‌آباد غریبه", quarry_region="عباس‌آباد"),
        lot_code="XX-1",
        b2c="2000000",
    )
    assert _codes(_rule_catalog(shop)) == {"AB-1", "AB-2"}


# --- public rendering -----------------------------------------------------------


@pytest.mark.django_db
def test_a_rule_catalog_renders_publicly_without_b2b_prices(client, shop):
    from apps.pricing.services import set_lot_price

    set_lot_price(lot=shop["abbas_one"], tier_code="b2b", amount=Decimal("1111111"))
    catalog = _rule_catalog(shop)

    url = reverse("catalog:shared_catalog", kwargs={"share_token": catalog.share_token})
    body = client.get(url).content.decode().replace(",", "")
    assert "تراورتن عباس‌آباد ۱" in body
    assert "2000000" in body
    assert "1111111" not in body


@pytest.mark.django_db
def test_a_deactivated_catalog_link_stops_working(client, shop):
    catalog = _rule_catalog(shop)
    update_catalog(catalog=catalog, membership=shop["membership"], is_active=False)

    url = reverse("catalog:shared_catalog", kwargs={"share_token": catalog.share_token})
    assert client.get(url).status_code == 404


@pytest.mark.django_db
def test_the_manage_page_shows_the_resolved_list_not_the_stored_one(client, shop):
    """A rule catalog stores no items, so showing stored rows would show nothing."""
    catalog = _rule_catalog(shop)
    client.force_login(shop["seller"].memberships.get(role="owner").user)
    session = client.session
    session["current_business_id"] = str(shop["seller"].id)
    session.save()

    body = client.get(reverse("catalog_manage:detail", kwargs={"catalog_id": catalog.id})).content.decode()
    assert "تراورتن عباس‌آباد ۱" in body
    assert "تراورتن عباس‌آباد ۲" in body
    assert "مرمریت نی‌ریز" not in body
