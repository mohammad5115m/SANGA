"""Product-bound purchase requests, and the accept/finalize split.

The rule most of this file exists to pin down: **accepting is not selling.** A
preliminary agreement that never becomes a shipment must not reach the books.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.businesses.models import Business
from apps.core.testing import make_business, make_item, make_product, owner_membership
from apps.inventory.models import InventoryLot
from apps.notifications.models import Notification
from apps.pricing.services import ensure_default_tiers
from apps.trading.models import PurchaseRequest, Trade
from apps.trading.services import (
    TradingError,
    cancel_purchase_request,
    create_purchase_request,
    finalize_sale,
    record_direct_sale,
    respond_to_purchase_request,
)


@pytest.fixture
def market(db):
    ensure_default_tiers()
    seller = make_business(name="سنگ فروشنده", owner_phone="09161110001", city="محلات")
    buyer = make_business(name="سنگ خریدار", owner_phone="09161110002", city="تهران")
    item = make_item(
        seller,
        product=make_product(seller, commercial_name="تراورتن عباس‌آباد"),
        lot_code="TRD-1",
        grade="سوپر",
        b2b="1500000",
        b2c="2000000",
    )
    return {
        "seller": seller,
        "buyer": buyer,
        "seller_m": owner_membership(seller),
        "buyer_m": owner_membership(buyer),
        "item": item,
    }


def _request(market, **kwargs) -> PurchaseRequest:
    params = {
        "buyer_business": market["buyer"],
        "membership": market["buyer_m"],
        "item": market["item"],
        "requested_qty_sqm": Decimal("50"),
        "proposed_unit_price": Decimal("1400000"),
    }
    params.update(kwargs)
    return create_purchase_request(**params)


def _accept(market, request_, **kwargs) -> PurchaseRequest:
    params = {"request": request_, "membership": market["seller_m"], "accept": True}
    params.update(kwargs)
    return respond_to_purchase_request(**params)


# --- a request always references a product ------------------------------------


@pytest.mark.django_db
def test_a_purchase_request_is_bound_to_one_product(market):
    request_ = _request(market)
    assert request_.item_id == market["item"].id
    assert request_.seller_business_id == market["seller"].id
    assert request_.status == PurchaseRequest.Status.SENT


@pytest.mark.django_db
def test_the_purchase_request_model_requires_a_product():
    """There is no free-form demand: the FK is not nullable."""
    field = PurchaseRequest._meta.get_field("item")
    assert field.null is False


@pytest.mark.django_db
@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda i: setattr(i, "is_visible", False), id="hidden"),
        pytest.param(
            lambda i: setattr(i, "availability_status", InventoryLot.Availability.UNAVAILABLE),
            id="unavailable",
        ),
    ],
)
def test_a_product_that_left_the_marketplace_cannot_be_requested(market, mutate):
    """The item is re-resolved server-side, not trusted from the page."""
    mutate(market["item"])
    market["item"].save()

    with pytest.raises(TradingError):
        _request(market)
    assert not PurchaseRequest.objects.exists()


@pytest.mark.django_db
def test_a_business_cannot_request_its_own_product(market):
    with pytest.raises(TradingError):
        create_purchase_request(
            buyer_business=market["seller"],
            membership=market["seller_m"],
            item=market["item"],
            requested_qty_sqm=Decimal("10"),
        )


@pytest.mark.django_db
def test_requesting_notifies_the_seller(market):
    _request(market)
    note = Notification.objects.filter(business=market["seller"]).first()
    assert note is not None
    assert "درخواست خرید" in note.title


# --- the seller may adjust the commercial terms -------------------------------


@pytest.mark.django_db
def test_seller_can_change_quantity_and_price_when_accepting(market):
    request_ = _request(market)
    _accept(market, request_, final_qty_sqm=Decimal("40"), final_unit_price=Decimal("1600000"))
    request_.refresh_from_db()

    # Both halves of the negotiation survive: what was asked, and what was agreed.
    assert request_.requested_qty_sqm == Decimal("50.000")
    assert request_.proposed_unit_price == Decimal("1400000.00")
    assert request_.final_qty_sqm == Decimal("40.000")
    assert request_.final_unit_price == Decimal("1600000.00")
    assert request_.agreed_total == Decimal("64000000.00")


@pytest.mark.django_db
def test_accepting_without_any_price_is_refused(market):
    request_ = _request(market, proposed_unit_price=None)
    with pytest.raises(TradingError):
        _accept(market, request_)


@pytest.mark.django_db
def test_rejecting_records_the_decision_and_notifies_the_buyer(market):
    request_ = _request(market)
    respond_to_purchase_request(
        request=request_,
        membership=market["seller_m"],
        accept=False,
        seller_note="موجودی نداریم",
    )
    request_.refresh_from_db()
    assert request_.status == PurchaseRequest.Status.REJECTED
    assert request_.seller_note == "موجودی نداریم"
    assert Notification.objects.filter(business=market["buyer"]).exists()


@pytest.mark.django_db
def test_a_request_cannot_be_answered_twice(market):
    request_ = _request(market)
    _accept(market, request_)
    with pytest.raises(TradingError):
        _accept(market, request_)


@pytest.mark.django_db
def test_another_business_cannot_answer_the_request(market):
    intruder = make_business(name="سنگ غریبه", owner_phone="09161110009")
    request_ = _request(market)
    with pytest.raises(TradingError):
        respond_to_purchase_request(
            request=request_,
            membership=owner_membership(intruder),
            accept=True,
            final_unit_price=Decimal("1"),
        )


# --- accepting is not selling -------------------------------------------------


@pytest.mark.django_db
def test_accepting_creates_no_trade(market):
    request_ = _request(market)
    _accept(market, request_)
    request_.refresh_from_db()

    assert request_.status == PurchaseRequest.Status.ACCEPTED
    assert not Trade.objects.exists(), "acceptance must not reach the books"


@pytest.mark.django_db
def test_finalizing_creates_exactly_one_trade(market):
    request_ = _request(market)
    _accept(market, request_, final_qty_sqm=Decimal("40"), final_unit_price=Decimal("1600000"))

    trade = finalize_sale(request=request_, membership=market["seller_m"])
    request_.refresh_from_db()

    assert Trade.objects.count() == 1
    assert request_.status == PurchaseRequest.Status.COMPLETED
    assert trade.quantity_sqm == Decimal("40.000")
    assert trade.unit_price == Decimal("1600000.00")
    assert trade.total_amount == Decimal("64000000.00")
    assert trade.buyer_business_id == market["buyer"].id


@pytest.mark.django_db
def test_finalizing_twice_produces_one_trade(market):
    """A double-click or a retried POST must not sell the same thing twice."""
    request_ = _request(market)
    _accept(market, request_)
    finalize_sale(request=request_, membership=market["seller_m"])

    with pytest.raises(TradingError):
        finalize_sale(request=request_, membership=market["seller_m"])
    assert Trade.objects.count() == 1


@pytest.mark.django_db
def test_an_unaccepted_request_cannot_be_finalized(market):
    request_ = _request(market)
    with pytest.raises(TradingError):
        finalize_sale(request=request_, membership=market["seller_m"])
    assert not Trade.objects.exists()


@pytest.mark.django_db
def test_finalizing_does_not_touch_stock(market):
    """SANGA does not know whether this was the only sale of that product."""
    before = market["item"].available_sqm
    request_ = _request(market)
    _accept(market, request_, final_qty_sqm=Decimal("40"))
    finalize_sale(request=request_, membership=market["seller_m"])

    market["item"].refresh_from_db()
    assert market["item"].available_sqm == before
    assert market["item"].availability_status == InventoryLot.Availability.AVAILABLE


# --- the trade is a historical snapshot ---------------------------------------


@pytest.mark.django_db
def test_a_trade_keeps_its_own_copy_of_what_was_sold(market):
    request_ = _request(market)
    _accept(market, request_)
    trade = finalize_sale(request=request_, membership=market["seller_m"])

    assert trade.product_name == "تراورتن عباس‌آباد"
    assert trade.grade == "سوپر"

    product = market["item"].product
    product.commercial_name = "نام کاملاً جدید"
    product.save(update_fields=["commercial_name"])
    market["item"].grade = "درجه سه"
    market["item"].save()

    trade.refresh_from_db()
    assert trade.product_name == "تراورتن عباس‌آباد"
    assert trade.grade == "سوپر"


@pytest.mark.django_db
def test_a_trade_survives_the_product_being_deleted(market):
    from apps.inventory.services import delete_item

    request_ = _request(market)
    _accept(market, request_)
    trade = finalize_sale(request=request_, membership=market["seller_m"])

    outcome = delete_item(lot=market["item"], membership=market["seller_m"])
    assert outcome == "archived", "a product with a trade must not be purged"

    trade.refresh_from_db()
    assert trade.product_name == "تراورتن عباس‌آباد"
    assert trade.total_amount == Decimal("70000000.00")


# --- buyer side ---------------------------------------------------------------


@pytest.mark.django_db
def test_buyer_can_cancel_an_open_request(market):
    request_ = _request(market)
    cancel_purchase_request(request=request_, membership=market["buyer_m"])
    request_.refresh_from_db()
    assert request_.status == PurchaseRequest.Status.CANCELLED


@pytest.mark.django_db
def test_seller_cannot_cancel_the_buyers_request(market):
    request_ = _request(market)
    with pytest.raises(TradingError):
        cancel_purchase_request(request=request_, membership=market["seller_m"])


# --- plan gates ---------------------------------------------------------------


@pytest.mark.django_db
def test_a_browse_only_seller_cannot_receive_requests(market):
    market["seller"].plan = Business.Plan.BROWSE
    market["seller"].save(update_fields=["plan"])
    with pytest.raises(TradingError):
        _request(market)


@pytest.mark.django_db
def test_a_browse_only_business_may_still_send_requests(market):
    """The whole point of the browse plan."""
    market["buyer"].plan = Business.Plan.BROWSE
    market["buyer"].save(update_fields=["plan"])
    request_ = _request(market)
    assert request_.status == PurchaseRequest.Status.SENT


@pytest.mark.django_db
def test_a_browse_only_seller_cannot_finalize(market):
    request_ = _request(market)
    _accept(market, request_)

    market["seller"].plan = Business.Plan.BROWSE
    market["seller"].save(update_fields=["plan"])
    with pytest.raises(TradingError):
        finalize_sale(request=request_, membership=market["seller_m"])


# --- direct sales -------------------------------------------------------------


@pytest.mark.django_db
def test_a_direct_sale_to_a_walk_in_customer_creates_no_user(market):
    from django.contrib.auth import get_user_model

    before = get_user_model().objects.count()
    trade = record_direct_sale(
        seller_business=market["seller"],
        membership=market["seller_m"],
        item=market["item"],
        quantity_sqm=Decimal("20"),
        unit_price=Decimal("2000000"),
        customer_name="آقای رضایی",
        customer_phone="09120001111",
    )
    assert trade.counterparty_type == Trade.Counterparty.CUSTOMER
    assert trade.buyer_business_id is None
    assert trade.counterparty_label == "آقای رضایی"
    assert get_user_model().objects.count() == before


@pytest.mark.django_db
def test_a_direct_sale_needs_a_counterparty(market):
    with pytest.raises(TradingError):
        record_direct_sale(
            seller_business=market["seller"],
            membership=market["seller_m"],
            item=market["item"],
            quantity_sqm=Decimal("20"),
            unit_price=Decimal("2000000"),
        )


@pytest.mark.django_db
def test_a_direct_sale_cannot_use_another_businesses_product(market):
    other = make_business(name="سنگ دیگر", owner_phone="09161110020")
    foreign = make_item(other, lot_code="FOR-1")
    with pytest.raises(TradingError):
        record_direct_sale(
            seller_business=market["seller"],
            membership=market["seller_m"],
            item=foreign,
            quantity_sqm=Decimal("1"),
            unit_price=Decimal("1"),
            customer_name="کسی",
        )


# --- pages --------------------------------------------------------------------


def _login(client, business):
    client.force_login(business.memberships.get(role="owner").user)
    session = client.session
    session["current_business_id"] = str(business.id)
    session.save()


@pytest.mark.django_db
def test_seller_sees_the_request_in_the_received_tab(client, market):
    _request(market)
    _login(client, market["seller"])
    body = client.get(reverse("trading:received_list")).content.decode("utf-8")
    assert "تراورتن عباس‌آباد" in body
    assert "سنگ خریدار" in body


@pytest.mark.django_db
def test_buyer_sees_it_in_the_sent_tab(client, market):
    _request(market)
    _login(client, market["buyer"])
    body = client.get(reverse("trading:sent_list")).content.decode("utf-8")
    assert "تراورتن عباس‌آباد" in body


@pytest.mark.django_db
def test_a_third_business_sees_neither(client, market):
    _request(market)
    intruder = make_business(name="سنگ غریبه", owner_phone="09161110030")
    _login(client, intruder)

    assert "تراورتن عباس‌آباد" not in client.get(reverse("trading:received_list")).content.decode("utf-8")
    assert "تراورتن عباس‌آباد" not in client.get(reverse("trading:sent_list")).content.decode("utf-8")


@pytest.mark.django_db
def test_the_accepted_page_says_the_sale_is_not_final_yet(client, market):
    request_ = _request(market)
    _accept(market, request_)

    _login(client, market["seller"])
    url = reverse("trading:received_detail", kwargs={"request_id": request_.id})
    body = client.get(url).content.decode("utf-8")
    assert "هنوز نهایی نشده" in body
    assert "نهایی کردن فروش" in body


@pytest.mark.django_db
def test_finalize_view_requires_confirmation(client, market):
    request_ = _request(market)
    _accept(market, request_)

    _login(client, market["seller"])
    url = reverse("trading:finalize", kwargs={"request_id": request_.id})
    client.post(url, {"note": ""})
    assert not Trade.objects.exists()

    client.post(url, {"note": "", "confirm": "on"})
    assert Trade.objects.count() == 1


@pytest.mark.django_db
def test_double_posting_the_finalize_form_creates_one_trade(client, market):
    request_ = _request(market)
    _accept(market, request_)

    _login(client, market["seller"])
    url = reverse("trading:finalize", kwargs={"request_id": request_.id})
    client.post(url, {"note": "", "confirm": "on"})
    client.post(url, {"note": "", "confirm": "on"}, follow=True)

    assert Trade.objects.count() == 1


@pytest.mark.django_db
def test_the_demand_board_is_gone(client, market):
    from django.urls import NoReverseMatch

    for name in ("purchase_requests:network_list", "purchase_requests:my_list"):
        with pytest.raises(NoReverseMatch):
            reverse(name)
