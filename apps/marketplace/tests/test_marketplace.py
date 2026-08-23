from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.accounting.models import LedgerEntry
from apps.businesses.models import Business
from apps.core.testing import expire_price, expire_stock, make_business, make_item, make_product, owner_membership
from apps.inventory.filters import ItemFilterSpec
from apps.inventory.models import InventoryLot
from apps.marketplace.models import PartnerInquiry
from apps.marketplace.selectors import filter_marketplace_lots, get_marketplace_lot, marketplace_lots_for
from apps.marketplace.services import convert_inquiry_to_invoice, create_grouped_inquiries, respond_to_inquiry
from apps.pricing.services import ensure_default_tiers


@pytest.fixture
def network(db):
    ensure_default_tiers()
    supplier = make_business(name="سنگ تأمین", owner_phone="09123330001", city="محلات")
    buyer = make_business(name="سنگ خریدار", owner_phone="09123330002", city="تهران")
    item = make_item(
        supplier,
        lot_code="SUP-1",
        product=make_product(supplier, commercial_name="تراورتن عباس‌آباد"),
        processing_type="ساب خورده",
        b2b="1000000",
        b2c="1600000",
    )
    return {"supplier": supplier, "buyer": buyer, "item": item}


def _login_owner(client, business):
    owner = business.memberships.get(role="owner").user
    client.force_login(owner)
    session = client.session
    session["current_business_id"] = str(business.id)
    session.save()
    return owner


# --- eligibility --------------------------------------------------------------


@pytest.mark.django_db
def test_colleague_sees_another_business_visible_item(network):
    assert network["item"] in marketplace_lots_for(network["buyer"])


@pytest.mark.django_db
def test_own_items_are_not_in_the_colleague_marketplace(network):
    assert network["item"] not in marketplace_lots_for(network["supplier"])


@pytest.mark.django_db
def test_hidden_item_is_not_in_the_marketplace(network):
    network["item"].is_visible = False
    network["item"].save()
    assert network["item"] not in marketplace_lots_for(network["buyer"])


@pytest.mark.django_db
def test_unavailable_item_is_not_in_the_marketplace(network):
    network["item"].availability_status = InventoryLot.Availability.UNAVAILABLE
    network["item"].save()
    assert network["item"] not in marketplace_lots_for(network["buyer"])


@pytest.mark.django_db
def test_deleted_item_is_not_in_the_marketplace(network):
    from django.utils import timezone

    network["item"].deleted_at = timezone.now()
    network["item"].save()
    assert network["item"] not in marketplace_lots_for(network["buyer"])


@pytest.mark.django_db
def test_draft_item_is_not_in_the_marketplace(network):
    network["item"].status = InventoryLot.Status.DRAFT
    network["item"].save()
    assert network["item"] not in marketplace_lots_for(network["buyer"])


@pytest.mark.django_db
def test_stale_stock_stays_in_the_marketplace(network):
    """«استعلام موجودی» is not «ناموجود»: the item remains discoverable."""
    expire_stock(network["item"])
    assert network["item"] in marketplace_lots_for(network["buyer"])


@pytest.mark.django_db
def test_expired_price_keeps_the_item_but_drops_the_number(network, client):
    expire_price(network["item"], "b2b")
    assert network["item"] in marketplace_lots_for(network["buyer"])

    _login_owner(client, network["buyer"])
    body = client.get(reverse("marketplace:home")).content.decode("utf-8")
    assert "تراورتن عباس‌آباد" in body
    assert "1000000" not in body.replace(",", "")
    assert "استعلام قیمت" in body


@pytest.mark.django_db
def test_suspended_viewer_sees_nothing(network):
    network["buyer"].status = Business.Status.SUSPENDED
    network["buyer"].save()
    assert list(marketplace_lots_for(network["buyer"])) == []


@pytest.mark.django_db
def test_suspended_supplier_disappears_from_the_marketplace(network):
    network["supplier"].status = Business.Status.SUSPENDED
    network["supplier"].save()
    assert list(marketplace_lots_for(network["buyer"])) == []


@pytest.mark.django_db
def test_detail_lookup_uses_the_same_gate_as_the_listing(network):
    network["item"].is_visible = False
    network["item"].save()
    assert get_marketplace_lot(network["buyer"], network["item"].id) is None


# --- price safety -------------------------------------------------------------


@pytest.mark.django_db
def test_marketplace_page_shows_b2b_and_never_b2c(network, client):
    _login_owner(client, network["buyer"])
    body = client.get(reverse("marketplace:home")).content.decode("utf-8")
    assert "1000000" in body.replace(",", "")
    assert "1600000" not in body.replace(",", "")


@pytest.mark.django_db
def test_marketplace_prefetch_loads_only_the_b2b_tier(network):
    item = marketplace_lots_for(network["buyer"]).first()
    loaded = {price.tier.code for price in item.prices.all()}
    assert loaded == {"b2b"}, "a B2C row must not even be in memory on a colleague page"


# --- shared filter engine -----------------------------------------------------


@pytest.mark.django_db
def test_filter_by_processing(network):
    qs = marketplace_lots_for(network["buyer"])
    assert network["item"] in filter_marketplace_lots(qs, spec=ItemFilterSpec(processing_type="ساب"))
    assert network["item"] not in filter_marketplace_lots(qs, spec=ItemFilterSpec(processing_type="چرمی"))


@pytest.mark.django_db
def test_filter_by_free_text_matches_several_fields(network):
    qs = marketplace_lots_for(network["buyer"])
    for term in ("تراورتن", "عباس‌آباد", "SUP-1"):
        assert network["item"] in filter_marketplace_lots(qs, spec=ItemFilterSpec(q=term)), term


@pytest.mark.django_db
def test_price_filter_uses_the_viewer_tier(network):
    """A colleague filtering by price is filtering B2B numbers, not B2C ones."""
    qs = marketplace_lots_for(network["buyer"])

    # 1,000,000 is the B2B price; 1,600,000 is B2C and must not be what matches.
    assert network["item"] in filter_marketplace_lots(
        qs, spec=ItemFilterSpec(price_min=Decimal("900000"), price_max=Decimal("1100000"))
    )
    assert network["item"] not in filter_marketplace_lots(
        qs, spec=ItemFilterSpec(price_min=Decimal("1500000"), price_max=Decimal("1700000"))
    )


@pytest.mark.django_db
def test_filter_combination_narrows(network):
    qs = marketplace_lots_for(network["buyer"])
    spec = ItemFilterSpec(stone="تراورتن", processing_type="ساب خورده")
    assert network["item"] in filter_marketplace_lots(qs, spec=spec)

    spec = ItemFilterSpec(stone="گرانیت", processing_type="ساب خورده")
    assert network["item"] not in filter_marketplace_lots(qs, spec=spec)


@pytest.mark.django_db
def test_filter_spec_round_trips_through_json():
    spec = ItemFilterSpec(
        stone="تراورتن",
        applications=["floor"],
        price_min=Decimal("100"),
        availability=InventoryLot.Availability.AVAILABLE,
    )
    restored = ItemFilterSpec.from_dict(spec.to_dict())
    assert restored.stone == "تراورتن"
    assert restored.applications == ["floor"]
    assert restored.price_min == Decimal("100")
    assert restored.availability == InventoryLot.Availability.AVAILABLE


@pytest.mark.django_db
def test_filter_spec_ignores_unusable_input():
    """A hand-edited query string or an older stored rule must not raise."""
    spec = ItemFilterSpec.from_dict(
        {"price_min": "not-a-number", "stock_mode": "bogus", "sort": "nope", "unknown_key": 1}
    )
    assert spec.price_min is None
    assert spec.sort == "recent"


@pytest.mark.django_db
def test_multi_seller_selection_creates_one_inquiry_per_seller():
    buyer = make_business(name="خریدار گروهی", owner_phone="09123330101")
    first = make_business(name="فروشنده اول", owner_phone="09123330102")
    second = make_business(name="فروشنده دوم", owner_phone="09123330103")
    first_item = make_item(first, lot_code="GROUP-1", b2b="100")
    second_item = make_item(second, lot_code="GROUP-2", b2b="200")
    batch = create_grouped_inquiries(
        buyer_business=buyer,
        user=buyer.memberships.get(role="owner").user,
        selections=[
            {"lot_id": first_item.id, "quantity": "2"},
            {"lot_id": second_item.id, "quantity": "3"},
        ],
    )
    assert batch.inquiries.count() == 2
    assert set(batch.inquiries.values_list("seller_business_id", flat=True)) == {first.id, second.id}
    assert PartnerInquiry.objects.get(seller_business=first).items.get().quantity_requested == Decimal("2")


@pytest.mark.django_db
def test_responded_inquiry_converts_to_one_financially_inert_invoice(network):
    batch = create_grouped_inquiries(
        buyer_business=network["buyer"],
        user=owner_membership(network["buyer"]).user,
        selections=[{"lot_id": network["item"].id, "quantity": "2"}],
    )
    inquiry = batch.inquiries.get()
    item = inquiry.items.get()
    seller_member = owner_membership(network["supplier"])
    respond_to_inquiry(
        inquiry=inquiry,
        membership=seller_member,
        offers={str(item.id): {"quantity": "2", "unit_price": "900000", "note": "موجود"}},
    )
    invoice = convert_inquiry_to_invoice(inquiry=inquiry, membership=seller_member)
    repeated = convert_inquiry_to_invoice(inquiry=inquiry, membership=seller_member)
    assert invoice.id == repeated.id
    assert invoice.status == "draft"
    assert invoice.buyer_business_id == network["buyer"].id
    assert invoice.items.get().quantity == Decimal("2")
    assert invoice.trade_id is None
    assert not LedgerEntry.objects.filter(related_invoice=invoice).exists()
