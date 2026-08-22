"""A role must be able to finish every workflow it can start.

The default staff role holds ``inventory.create`` and ``sale.finalize`` but not
``prices.edit`` or ``invoice.manage``. The role matrix and the workflows were
designed independently, so both of those combinations produced a dead end that
only appeared after the user had committed to the action:

- the old add-product wizard always wrote prices, so staff got an error *after* the
  draft had been saved, and had to find and clean up the orphan themselves;
- finalizing a sale swallowed the invoice failure, so the ledger moved, no
  document existed, and the trade page offered no way to ask for one.

Neither is fixed by giving staff more permissions. A salesperson who cannot set
prices is a deliberate product rule; the workflows had to stop assuming they
could.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.accounting.selectors import current_balance
from apps.businesses.models import BusinessMembership
from apps.core.testing import make_business, make_item, make_product, make_user, owner_membership
from apps.inventory.models import InventoryLot, VocabularyTerm
from apps.invoicing.models import SalesInvoice
from apps.pricing.services import ensure_default_tiers, resolve_visible_prices
from apps.trading.services import record_direct_sale


@pytest.fixture
def shop(db):
    ensure_default_tiers()
    seller = make_business(name="سنگ کارمند", owner_phone="09371110001")
    buyer = make_business(name="سنگ خریدار", owner_phone="09371110002")
    seller.seat_limit = 5
    seller.save(update_fields=["seat_limit"])
    staff = BusinessMembership.objects.create(
        user=make_user("09371110003"),
        business=seller,
        role=BusinessMembership.Role.STAFF,
    )
    return {"seller": seller, "buyer": buyer, "staff": staff, "owner": owner_membership(seller)}


def _login(client, membership) -> None:
    client.force_login(membership.user)
    session = client.session
    session["current_business_id"] = str(membership.business_id)
    session.save()


def _product_payload():
    return {
        "stone": VocabularyTerm.objects.get(name="تراورتن").id,
        "name_suffix": "کارمند",
        "processing_type": "ساب خورده",
        "available_sqm": "150",
        "stock_valid_for_days": "7",
        "availability_status": InventoryLot.Availability.AVAILABLE,
    }


def test_the_default_staff_role_is_the_one_under_test(shop):
    staff = shop["staff"]
    assert staff.has_capability("inventory.create")
    assert staff.has_capability("sale.finalize")
    assert not staff.has_capability("prices.edit")
    assert not staff.has_capability("invoice.manage")


# --- adding a product ---------------------------------------------------------


@pytest.mark.django_db
def test_staff_can_complete_the_unified_product_form(client, shop):
    _login(client, shop["staff"])
    response = client.post(reverse("inventory:product_create"), _product_payload(), follow=True)

    assert response.status_code == 200
    lot = InventoryLot.objects.get(business=shop["seller"])
    assert lot.available_sqm == Decimal("150.000")


@pytest.mark.django_db
def test_a_product_staff_created_is_priced_by_inquiry_not_left_half_made(client, shop):
    """Not an orphan draft and not a silent zero: «استعلام قیمت» is the honest
    display for a price nobody has set yet."""
    _login(client, shop["staff"])
    client.post(reverse("inventory:product_create"), _product_payload(), follow=True)

    lot = InventoryLot.objects.get(business=shop["seller"])
    assert lot.prices.count() == 0
    assert resolve_visible_prices(lot, "b2c_public") == {}


@pytest.mark.django_db
def test_the_form_does_not_offer_staff_price_fields_they_cannot_use(client, shop):
    _login(client, shop["staff"])
    body = client.get(reverse("inventory:product_create")).content.decode()
    assert "قیمت همکار" not in body


@pytest.mark.django_db
def test_a_failed_price_write_leaves_no_orphan_draft(shop):
    """Creation and pricing are one transaction, so a rejected price rolls the
    whole thing back rather than saving half of it."""
    from apps.inventory.services import InventoryError, create_product_item

    before = InventoryLot.objects.filter(business=shop["seller"]).count()
    with pytest.raises(InventoryError):
        create_product_item(
            business=shop["seller"],
            membership=shop["staff"],
            product_fields={
                "stone": VocabularyTerm.objects.get(name="تراورتن"),
                "name_suffix": "اتمی",
            },
            item_fields={"available_sqm": Decimal("10")},
            b2c_price={"mode": "fixed", "amount": Decimal("1000")},
        )
    assert InventoryLot.objects.filter(business=shop["seller"]).count() == before


# --- finalizing a sale --------------------------------------------------------


@pytest.mark.django_db
def test_staff_finalizing_a_sale_gets_a_draft_invoice_not_silence(shop):
    """The invoice is a consequence of the sale, not a second decision. It used
    to be swallowed, leaving a moved ledger and no document."""
    item = make_item(
        shop["seller"],
        product=make_product(shop["seller"], commercial_name="تراورتن فروش"),
        lot_code="ST-5",
        b2b="1000000",
    )
    trade = record_direct_sale(
        seller_business=shop["seller"],
        membership=shop["staff"],
        item=item,
        quantity_sqm=Decimal("20"),
        unit_price=Decimal("1000000"),
        buyer_business=shop["buyer"],
    )

    invoice = SalesInvoice.objects.get(trade=trade)
    assert invoice.status == SalesInvoice.Status.DRAFT
    assert current_balance(shop["seller"], shop["buyer"]) == Decimal("20000000.00")


@pytest.mark.django_db
def test_an_owner_finalizing_a_sale_still_gets_an_issued_invoice(shop):
    item = make_item(
        shop["seller"],
        product=make_product(shop["seller"], commercial_name="تراورتن مالک"),
        lot_code="ST-6",
        b2b="1000000",
    )
    trade = record_direct_sale(
        seller_business=shop["seller"],
        membership=shop["owner"],
        item=item,
        quantity_sqm=Decimal("20"),
        unit_price=Decimal("1000000"),
        buyer_business=shop["buyer"],
    )
    assert SalesInvoice.objects.get(trade=trade).status == SalesInvoice.Status.ISSUED


@pytest.mark.django_db
def test_a_trade_without_an_invoice_offers_a_way_to_create_one(client, shop):
    item = make_item(
        shop["seller"],
        product=make_product(shop["seller"], commercial_name="تراورتن بازیابی"),
        lot_code="ST-7",
        b2b="1000000",
    )
    trade = record_direct_sale(
        seller_business=shop["seller"],
        membership=shop["owner"],
        item=item,
        quantity_sqm=Decimal("20"),
        unit_price=Decimal("1000000"),
        buyer_business=shop["buyer"],
    )
    SalesInvoice.objects.filter(trade=trade).delete()

    _login(client, shop["staff"])
    body = client.get(reverse("trading:trade_detail", kwargs={"trade_id": trade.id})).content.decode()
    assert "ساخت فاکتور" in body

    client.post(reverse("trading:trade_create_invoice", kwargs={"trade_id": trade.id}))
    assert SalesInvoice.objects.filter(trade=trade).count() == 1


@pytest.mark.django_db
def test_the_recovery_action_cannot_produce_a_second_invoice(client, shop):
    item = make_item(
        shop["seller"],
        product=make_product(shop["seller"], commercial_name="تراورتن تکراری"),
        lot_code="ST-8",
        b2b="1000000",
    )
    trade = record_direct_sale(
        seller_business=shop["seller"],
        membership=shop["owner"],
        item=item,
        quantity_sqm=Decimal("20"),
        unit_price=Decimal("1000000"),
        buyer_business=shop["buyer"],
    )

    _login(client, shop["owner"])
    client.post(reverse("trading:trade_create_invoice", kwargs={"trade_id": trade.id}))
    client.post(reverse("trading:trade_create_invoice", kwargs={"trade_id": trade.id}))

    assert SalesInvoice.objects.filter(trade=trade).count() == 1


@pytest.mark.django_db
def test_another_business_cannot_create_an_invoice_for_someone_elses_trade(client, shop):
    item = make_item(
        shop["seller"],
        product=make_product(shop["seller"], commercial_name="تراورتن غریبه"),
        lot_code="ST-9",
        b2b="1000000",
    )
    trade = record_direct_sale(
        seller_business=shop["seller"],
        membership=shop["owner"],
        item=item,
        quantity_sqm=Decimal("20"),
        unit_price=Decimal("1000000"),
        buyer_business=shop["buyer"],
    )
    SalesInvoice.objects.filter(trade=trade).delete()

    _login(client, owner_membership(shop["buyer"]))
    client.post(reverse("trading:trade_create_invoice", kwargs={"trade_id": trade.id}))
    assert not SalesInvoice.objects.filter(trade=trade).exists()
