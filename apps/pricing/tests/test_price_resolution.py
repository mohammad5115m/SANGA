from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.core.testing import expire_price, make_business, make_item
from apps.pricing.models import LotPrice
from apps.pricing.services import (
    confirm_lot_price,
    ensure_default_tiers,
    resolve_visible_prices,
    set_lot_price,
)


@pytest.fixture
def seller(db):
    ensure_default_tiers()
    return make_business(name="سنگ قیمت", owner_phone="09121110001")


@pytest.fixture
def item(seller):
    return make_item(seller, b2b="1000000", b2c="1600000")


# --- audience isolation -------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("audience", "expected"),
    [
        ("b2c_public", {"b2c"}),
        ("b2b_partner", {"b2b"}),
        ("owner_staff", {"b2b", "b2c"}),
        ("platform_admin", {"b2b", "b2c"}),
    ],
)
def test_disallowed_tiers_are_absent_not_blanked(item, audience, expected):
    """Absence is the protection: callers serialize this dict into templates."""
    visible = resolve_visible_prices(item, audience)
    assert set(visible.keys()) == expected


@pytest.mark.django_db
def test_public_resolution_never_mentions_the_b2b_amount(item):
    visible = resolve_visible_prices(item, "b2c_public")
    assert "1000000" not in str(visible)


@pytest.mark.django_db
def test_owner_without_price_capability_sees_nothing(item):
    assert resolve_visible_prices(item, "owner_staff", can_view_prices=False) == {}


@pytest.mark.django_db
def test_tier_filtering_survives_a_prefetch_cache(seller):
    """List pages prefetch one tier; resolution must still not widen the set."""
    from apps.inventory.policy import eligible_items

    make_item(seller, b2b="1000000", b2c="1600000")
    loaded = eligible_items(audience="public").first()

    assert "prices" in loaded._prefetched_objects_cache
    assert set(resolve_visible_prices(loaded, "b2c_public").keys()) == {"b2c"}


# --- price modes --------------------------------------------------------------


@pytest.mark.django_db
def test_inquiry_mode_stores_no_amount(seller):
    item = make_item(seller)
    set_lot_price(lot=item, tier_code="b2c", amount=None, mode=LotPrice.Mode.INQUIRY)

    price = item.prices.get(tier__code="b2c")
    assert price.amount is None
    view = resolve_visible_prices(item, "b2c_public")["b2c"]
    assert view.display_as_inquiry is True
    assert view.amount is None
    assert view.expired is False, "a deliberate inquiry price is not an expired one"


@pytest.mark.django_db
def test_fixed_price_requires_an_amount(seller):
    item = make_item(seller)
    with pytest.raises(ValueError):
        set_lot_price(lot=item, tier_code="b2c", amount=None, mode=LotPrice.Mode.FIXED)


@pytest.mark.django_db
@pytest.mark.parametrize("amount", ["NaN", "Infinity", "100000000000000"])
def test_invalid_or_oversized_price_is_rejected_before_persistence(seller, amount):
    item = make_item(seller)

    with pytest.raises(ValueError, match="مبلغ"):
        set_lot_price(lot=item, tier_code="b2c", amount=amount)

    assert not item.prices.exists()


# --- freshness ----------------------------------------------------------------


@pytest.mark.django_db
def test_expired_fixed_price_hides_the_number(item):
    expire_price(item, "b2c")
    item.refresh_from_db()

    view = resolve_visible_prices(item, "b2c_public")["b2c"]
    assert view.amount is None
    assert view.display_as_inquiry is True
    assert view.expired is True

    # The stored figure survives so the seller can see what they last set.
    assert item.prices.get(tier__code="b2c").amount == Decimal("1600000.00")


@pytest.mark.django_db
def test_b2b_and_b2c_expire_independently(item):
    expire_price(item, "b2c")
    item.refresh_from_db()

    assert resolve_visible_prices(item, "b2c_public")["b2c"].amount is None
    assert resolve_visible_prices(item, "b2b_partner")["b2b"].amount == Decimal("1000000.00")


@pytest.mark.django_db
def test_confirming_a_price_restores_the_number(item, seller):
    from apps.core.testing import owner_membership

    expire_price(item, "b2c")
    item.refresh_from_db()
    assert resolve_visible_prices(item, "b2c_public")["b2c"].amount is None

    confirm_lot_price(lot=item, tier_code="b2c", membership=owner_membership(seller))
    item.refresh_from_db()

    view = resolve_visible_prices(item, "b2c_public")["b2c"]
    assert view.amount == Decimal("1600000.00")


@pytest.mark.django_db
def test_price_freshness_is_independent_of_stock_freshness(seller):
    """A seller may trust their stock for ten days and their price for two."""
    from apps.core.testing import expire_stock
    from apps.inventory.freshness import StockDisplay, stock_view

    item = make_item(seller, stock_valid_for_days=10, b2c="500000")
    expire_stock(item)

    assert stock_view(item).display == StockDisplay.INQUIRY
    assert resolve_visible_prices(item, "b2c_public")["b2c"].amount == Decimal("500000.00")


# --- special sale -------------------------------------------------------------


@pytest.mark.django_db
def test_special_price_replaces_the_normal_amount_for_that_audience_only(seller):
    item = make_item(seller, b2b="1000000", b2c="1600000")
    set_lot_price(
        lot=item,
        tier_code="b2c",
        amount=Decimal("1600000"),
        special_amount=Decimal("1200000"),
        special_until=timezone.now() + timedelta(days=2),
    )

    b2c = resolve_visible_prices(item, "b2c_public")["b2c"]
    assert b2c.amount == Decimal("1200000.00")
    assert b2c.is_special is True

    # The colleague channel is untouched, which is the point of putting the
    # special price on the tier row rather than on the item.
    b2b = resolve_visible_prices(item, "b2b_partner")["b2b"]
    assert b2b.amount == Decimal("1000000.00")
    assert b2b.is_special is False


@pytest.mark.django_db
def test_a_b2b_special_price_is_never_visible_publicly(seller):
    item = make_item(seller, b2b="1000000", b2c="1600000")
    set_lot_price(
        lot=item,
        tier_code="b2b",
        amount=Decimal("1000000"),
        special_amount=Decimal("900000"),
        special_until=timezone.now() + timedelta(days=2),
    )
    public = resolve_visible_prices(item, "b2c_public")
    assert "900000" not in str(public)
    assert "b2b" not in public


@pytest.mark.django_db
def test_expired_special_sale_falls_back_to_the_normal_price(seller):
    item = make_item(seller, b2c="1600000")
    price = item.prices.get(tier__code="b2c")
    price.special_amount = Decimal("1200000")
    price.special_until = timezone.now() - timedelta(hours=1)
    price.save()

    view = resolve_visible_prices(item, "b2c_public")["b2c"]
    assert view.amount == Decimal("1600000.00")
    assert view.is_special is False


@pytest.mark.django_db
def test_special_sale_cannot_outlive_regular_price_freshness(seller):
    item = make_item(seller, b2c="1600000")
    set_lot_price(
        lot=item,
        tier_code="b2c",
        amount=Decimal("1600000"),
        valid_for_days=1,
        special_amount=Decimal("1200000"),
        special_until=timezone.now() + timedelta(days=4),
    )
    expire_price(item, "b2c")
    item.refresh_from_db()
    view = resolve_visible_prices(item, "b2c_public")["b2c"]
    assert view.amount is None
    assert view.is_special is False


@pytest.mark.django_db
def test_special_sale_requires_an_end_date(seller):
    item = make_item(seller, b2c="1600000")
    with pytest.raises(ValueError):
        set_lot_price(
            lot=item,
            tier_code="b2c",
            amount=Decimal("1600000"),
            special_amount=Decimal("1000000"),
            special_until=None,
        )


@pytest.mark.django_db
def test_no_contact_specific_pricing_remains(seller):
    """V2 has exactly two price channels."""
    import apps.pricing.models as pricing_models

    assert not hasattr(pricing_models, "ContactPrice")
    item = make_item(seller, b2b="1", b2c="2")
    assert set(item.prices.values_list("tier__code", flat=True)) == {"b2b", "b2c"}
