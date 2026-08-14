from decimal import Decimal

import pytest
from django.utils import timezone

from apps.core.testing import expire_price, make_business, make_item, make_product
from apps.inventory.filters import ItemFilterSpec, effective_price_bounds
from apps.inventory.policy import eligible_items
from apps.pricing.services import ensure_default_tiers, resolve_visible_prices, set_lot_price


@pytest.fixture
def market(db):
    ensure_default_tiers()
    seller = make_business(name="سنگ تازگی", owner_phone="09351110001")
    viewer = make_business(name="سنگ بیننده", owner_phone="09351110002")
    return seller, viewer


def _item(market, code, *, stone="تراورتن", processing="ساب خورده", **kwargs):
    seller, _viewer = market
    return make_item(
        seller,
        product=make_product(seller, commercial_name=f"{stone} {code}", stone_type=stone),
        lot_code=code,
        processing_type=processing,
        **kwargs,
    )


def _codes(qs):
    return {item.lot_code for item in qs}


def _public(spec):
    return spec.apply(eligible_items(audience="public"), audience="public")


@pytest.mark.django_db
def test_inquiry_price_remains_discoverable_without_a_range(market):
    inquiry = _item(market, "T-INQ101")
    set_lot_price(lot=inquiry, tier_code="b2c", amount=None, mode="inquiry")
    assert _codes(_public(ItemFilterSpec())) == {inquiry.lot_code}
    assert _codes(_public(ItemFilterSpec(price_max=Decimal("9999999")))) == set()


@pytest.mark.django_db
def test_expired_price_cannot_answer_a_price_range(market):
    fresh = _item(market, "T-FRE101", b2c="1000")
    stale = _item(market, "T-STA101", b2c="1000")
    expire_price(stale, "b2c")
    assert resolve_visible_prices(stale, "b2c_public")["b2c"].amount is None
    result = _public(ItemFilterSpec(price_min=Decimal("500"), price_max=Decimal("1500")))
    assert _codes(result) == {fresh.lot_code}


@pytest.mark.django_db
def test_live_special_price_is_used_by_filter_and_bounds(market):
    special = _item(market, "T-SPE101", b2c="1000")
    normal = _item(market, "T-NOR101", b2c="800")
    set_lot_price(
        lot=special,
        tier_code="b2c",
        amount=Decimal("1000"),
        special_amount=Decimal("400"),
        special_until=timezone.now() + timezone.timedelta(days=3),
    )
    assert _codes(_public(ItemFilterSpec(price_max=Decimal("500")))) == {special.lot_code}
    bounds = effective_price_bounds(
        eligible_items(audience="public"), spec=ItemFilterSpec(), audience="public"
    )
    assert bounds == (Decimal("400"), Decimal("800"))


@pytest.mark.django_db
def test_price_bounds_follow_non_price_filters(market):
    _item(market, "T-SAB101", processing="ساب خورده", b2c="100")
    _item(market, "T-CHA101", processing="چرمی", b2c="900")
    bounds = effective_price_bounds(
        eligible_items(audience="public"),
        spec=ItemFilterSpec(processing_type="چرمی"),
        audience="public",
    )
    assert bounds == (Decimal("900"), Decimal("900"))


@pytest.mark.django_db
def test_public_and_colleague_price_filters_use_different_tiers(market):
    _seller, viewer = market
    item = _item(market, "T-TIE101", b2b="100", b2c="9000")
    assert _codes(_public(ItemFilterSpec(price_max=Decimal("500")))) == set()
    colleague = ItemFilterSpec(price_max=Decimal("500")).apply(
        eligible_items(audience="colleague", viewer_business=viewer), audience="colleague"
    )
    assert _codes(colleague) == {item.lot_code}


@pytest.mark.django_db
def test_stone_processing_and_availability_are_the_compact_filters(market):
    chosen = _item(market, "T-CHO101", stone="مرمریت", processing="چرمی", b2c="100")
    _item(market, "T-OTH101", stone="تراورتن", processing="چرمی", b2c="100")
    spec = ItemFilterSpec(stone="مرمریت", processing_type="چرمی", availability="available")
    assert _codes(_public(spec)) == {chosen.lot_code}
