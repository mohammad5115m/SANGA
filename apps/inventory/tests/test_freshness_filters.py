"""What a filter returns must be what a card says.

An item whose confirmed quantity has expired reads «استعلام موجودی», and one
whose fixed price has expired reads «استعلام قیمت». The filters used to compare
the stored columns instead, so «حداقل ۱۰۰ متر» returned items that, on the very
same page, said they had no current quantity — and a price range returned items
whose own card refused to quote a price.

Every test here pairs the query result with the displayed state, because either
one alone can look correct while the two disagree.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.utils import timezone

from apps.core.testing import (
    expire_price,
    expire_stock,
    make_business,
    make_item,
    make_product,
    owner_membership,
)
from apps.inventory.filters import ItemFilterSpec
from apps.inventory.freshness import StockDisplay, stock_view
from apps.inventory.models import InventoryLot
from apps.inventory.policy import eligible_items
from apps.inventory.services import confirm_item_stock
from apps.pricing.services import ensure_default_tiers, resolve_visible_prices, set_lot_price


@pytest.fixture
def market(db):
    ensure_default_tiers()
    seller = make_business(name="سنگ تازگی", owner_phone="09351110001")
    viewer = make_business(name="سنگ بیننده", owner_phone="09351110002")
    return {"seller": seller, "viewer": viewer, "membership": owner_membership(seller)}


def _item(market, code: str, **kwargs) -> InventoryLot:
    return make_item(
        market["seller"],
        product=make_product(market["seller"], commercial_name=f"تراورتن {code}"),
        lot_code=code,
        **kwargs,
    )


def _public(spec: ItemFilterSpec):
    return spec.apply(eligible_items(audience="public"), audience="public")


def _codes(qs) -> set[str]:
    return {lot.lot_code for lot in qs}


# --- stock ---------------------------------------------------------------------


@pytest.mark.django_db
def test_an_expired_quantity_cannot_answer_a_minimum_quantity_filter(market):
    fresh = _item(market, "FR-1", available_sqm="500", b2c="100")
    stale = _item(market, "ST-1", available_sqm="500", b2c="100")
    expire_stock(stale)

    assert stock_view(stale).display == StockDisplay.INQUIRY, "precondition: the card says inquiry"

    result = _codes(_public(ItemFilterSpec(min_qty_sqm=Decimal("100"))))
    assert result == {fresh.lot_code}
    assert stale.lot_code not in result


@pytest.mark.django_db
def test_an_expired_quantity_is_still_discoverable_without_the_filter(market):
    """Stale is not hidden — that is «ناموجود», a different thing entirely."""
    stale = _item(market, "ST-2", available_sqm="500", b2c="100")
    expire_stock(stale)
    assert _codes(_public(ItemFilterSpec())) == {stale.lot_code}


@pytest.mark.django_db
def test_unlimited_stock_satisfies_any_minimum_while_it_is_fresh(market):
    unlimited = _item(market, "UN-1", stock_mode=InventoryLot.StockMode.UNLIMITED, b2c="100")
    assert _codes(_public(ItemFilterSpec(min_qty_sqm=Decimal("9999")))) == {unlimited.lot_code}


@pytest.mark.django_db
def test_expired_unlimited_stock_stops_satisfying_a_minimum(market):
    """An unlimited claim is a claim too, and it goes stale like any other."""
    unlimited = _item(market, "UN-2", stock_mode=InventoryLot.StockMode.UNLIMITED, b2c="100")
    expire_stock(unlimited)

    assert stock_view(unlimited).display == StockDisplay.INQUIRY
    assert _codes(_public(ItemFilterSpec(min_qty_sqm=Decimal("1")))) == set()


@pytest.mark.django_db
def test_filtering_by_stock_mode_uses_the_effective_mode(market):
    fresh = _item(market, "FR-2", available_sqm="500", b2c="100")
    stale = _item(market, "ST-3", available_sqm="500", b2c="100")
    expire_stock(stale)

    exact = _codes(_public(ItemFilterSpec(stock_mode=InventoryLot.StockMode.EXACT)))
    inquiry = _codes(_public(ItemFilterSpec(stock_mode=InventoryLot.StockMode.INQUIRY)))

    assert exact == {fresh.lot_code}
    assert inquiry == {stale.lot_code}


@pytest.mark.django_db
def test_a_deliberate_inquiry_item_is_found_by_the_inquiry_filter(market):
    chosen = _item(market, "IQ-1", stock_mode=InventoryLot.StockMode.INQUIRY, b2c="100")
    assert _codes(_public(ItemFilterSpec(stock_mode=InventoryLot.StockMode.INQUIRY))) == {chosen.lot_code}


@pytest.mark.django_db
def test_reconfirming_puts_the_item_back_in_quantity_results(market):
    stale = _item(market, "ST-4", available_sqm="500", b2c="100")
    expire_stock(stale)
    assert _codes(_public(ItemFilterSpec(min_qty_sqm=Decimal("100")))) == set()

    confirm_item_stock(lot=stale, membership=market["membership"], available_sqm=Decimal("500"))
    assert _codes(_public(ItemFilterSpec(min_qty_sqm=Decimal("100")))) == {stale.lot_code}


# --- price ---------------------------------------------------------------------


@pytest.mark.django_db
def test_an_expired_price_cannot_answer_a_price_range(market):
    fresh = _item(market, "PF-1", b2c="1000")
    stale = _item(market, "PS-1", b2c="1000")
    expire_price(stale, "b2c")

    assert resolve_visible_prices(stale, "b2c_public")["b2c"].amount is None

    result = _codes(_public(ItemFilterSpec(price_min=Decimal("500"), price_max=Decimal("1500"))))
    assert result == {fresh.lot_code}


@pytest.mark.django_db
def test_an_inquiry_priced_item_never_answers_a_price_range(market):
    _item(market, "PI-1", b2c=None)
    priced = _item(market, "PF-2", b2c="1000")
    set_lot_price(lot=InventoryLot.objects.get(lot_code="PI-1"), tier_code="b2c", amount=None)

    assert _codes(_public(ItemFilterSpec(price_max=Decimal("99999")))) == {priced.lot_code}


@pytest.mark.django_db
def test_a_live_special_price_is_the_one_that_is_filtered_on(market):
    """The special is the number on the card, so it is the number that counts."""
    item = _item(market, "SP-1", b2c="1000")
    set_lot_price(
        lot=item,
        tier_code="b2c",
        amount=Decimal("1000"),
        special_amount=Decimal("400"),
        special_until=timezone.now() + timezone.timedelta(days=3),
    )

    assert _codes(_public(ItemFilterSpec(price_max=Decimal("500")))) == {item.lot_code}
    assert _codes(_public(ItemFilterSpec(price_min=Decimal("900")))) == set()


@pytest.mark.django_db
def test_an_expired_special_falls_back_to_the_standard_price(market):
    item = _item(market, "SP-2", b2c="1000")
    set_lot_price(
        lot=item,
        tier_code="b2c",
        amount=Decimal("1000"),
        special_amount=Decimal("400"),
        special_until=timezone.now() - timezone.timedelta(days=1),
    )

    assert _codes(_public(ItemFilterSpec(price_max=Decimal("500")))) == set()
    assert _codes(_public(ItemFilterSpec(price_min=Decimal("900")))) == {item.lot_code}


@pytest.mark.django_db
def test_only_special_finds_live_sales_and_not_expired_ones(market):
    live = _item(market, "SP-3", b2c="1000")
    over = _item(market, "SP-4", b2c="1000")
    set_lot_price(lot=live, tier_code="b2c", amount=Decimal("1000"), special_amount=Decimal("400"))
    set_lot_price(
        lot=over,
        tier_code="b2c",
        amount=Decimal("1000"),
        special_amount=Decimal("400"),
        special_until=timezone.now() - timezone.timedelta(hours=1),
    )

    assert _codes(_public(ItemFilterSpec(only_special=True))) == {live.lot_code}


@pytest.mark.django_db
def test_price_sorting_uses_the_price_the_viewer_is_shown(market):
    cheap = _item(market, "SO-1", b2c="100")
    dear = _item(market, "SO-2", b2c="900")
    stale = _item(market, "SO-3", b2c="1")
    expire_price(stale, "b2c")

    order = [lot.lot_code for lot in _public(ItemFilterSpec(sort="price_asc"))]
    assert order.index(cheap.lot_code) < order.index(dear.lot_code)
    assert order[-1] == stale.lot_code, "an item with no current price must not lead «ارزان‌ترین»"


@pytest.mark.django_db
def test_the_b2b_price_never_answers_a_public_price_filter(market):
    """Audience isolation survives the rewrite: the annotation is tier-scoped."""
    item = _item(market, "TI-1", b2b="100", b2c="9000")

    assert _codes(_public(ItemFilterSpec(price_max=Decimal("500")))) == set()
    colleague = ItemFilterSpec(price_max=Decimal("500")).apply(
        eligible_items(audience="colleague", viewer_business=market["viewer"]),
        audience="colleague",
    )
    assert _codes(colleague) == {item.lot_code}


@pytest.mark.django_db
def test_a_price_filter_does_not_duplicate_rows(market):
    """A subquery rather than a join, so no .distinct() is needed to undo damage."""
    _item(market, "DP-1", b2b="500", b2c="500")
    assert _public(ItemFilterSpec(price_min=Decimal("1"))).count() == 1


# --- combined ------------------------------------------------------------------


@pytest.mark.django_db
def test_stock_and_price_freshness_are_independent(market):
    item = _item(market, "MX-1", available_sqm="500", b2c="1000")
    expire_price(item, "b2c")

    assert _codes(_public(ItemFilterSpec(min_qty_sqm=Decimal("100")))) == {item.lot_code}
    assert _codes(_public(ItemFilterSpec(price_max=Decimal("99999")))) == set()


# --- the confirmation form ------------------------------------------------------


@pytest.mark.django_db
def test_the_confirmation_form_persists_the_validity_it_was_given(client, market):
    """AUD-016. The screen asked how long to trust the number and the view threw
    the answer away, so the seller was told it had been saved while the old
    window stayed in force."""
    from django.urls import reverse

    item = _item(market, "CV-1", available_sqm="500", stock_valid_for_days=7, b2c="100")
    client.force_login(market["membership"].user)
    session = client.session
    session["current_business_id"] = str(market["seller"].id)
    session.save()

    client.post(
        reverse("inventory:lot_confirm_stock", kwargs={"lot_id": item.id}),
        {"stock_mode": InventoryLot.StockMode.EXACT, "available_sqm": "500", "stock_valid_for_days": "30"},
    )

    item.refresh_from_db()
    assert item.stock_valid_for_days == 30
    assert (item.stock_expires_at - item.stock_confirmed_at).days == 30
