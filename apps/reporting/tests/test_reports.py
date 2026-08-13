"""Reports, validated against fixtures whose totals are known by hand.

A report that is merely "not crashing" is worthless — the whole value is that
the number is right — so every assertion here is against an arithmetic result
computed in the test, not against whatever the code produced.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounting.models import LedgerEntry
from apps.accounting.services import post_manual_entry, reverse_entry
from apps.core.testing import expire_stock, make_business, make_item, make_product, owner_membership
from apps.invoicing.services import cancel_invoice, create_manual_invoice
from apps.pricing.services import ensure_default_tiers
from apps.reporting import reports
from apps.reporting.reports import DateRange
from apps.trading.services import record_direct_sale


@pytest.fixture
def books(db):
    """Two colleagues, three sales, known totals.

        travertine → colleague A : 40 m² × 1,000,000 = 40,000,000  (today)
        travertine → colleague A : 10 m² × 1,000,000 = 10,000,000  (100 days ago)
        granite    → walk-in     : 20 m² × 2,000,000 = 40,000,000  (today)

        grand total 90,000,000 over 70 m²
    """
    ensure_default_tiers()
    seller = make_business(name="سنگ فروشنده", owner_phone="09231110001")
    colleague = make_business(name="سنگ همکار الف", owner_phone="09231110002")
    membership = owner_membership(seller)

    travertine = make_item(
        seller,
        product=make_product(seller, commercial_name="تراورتن عباس‌آباد", stone_type="تراورتن"),
        lot_code="TR-1",
        b2b="1000000",
    )
    granite = make_item(
        seller,
        product=make_product(seller, commercial_name="گرانیت نطنز", stone_type="گرانیت"),
        lot_code="GR-1",
        b2b="2000000",
    )

    recent = record_direct_sale(
        seller_business=seller,
        membership=membership,
        item=travertine,
        quantity_sqm=Decimal("40"),
        unit_price=Decimal("1000000"),
        buyer_business=colleague,
    )
    old = record_direct_sale(
        seller_business=seller,
        membership=membership,
        item=travertine,
        quantity_sqm=Decimal("10"),
        unit_price=Decimal("1000000"),
        buyer_business=colleague,
    )
    old.finalized_at = timezone.now() - timedelta(days=100)
    old.save(update_fields=["finalized_at"])

    record_direct_sale(
        seller_business=seller,
        membership=membership,
        item=granite,
        quantity_sqm=Decimal("20"),
        unit_price=Decimal("2000000"),
        customer_name="آقای رضایی",
    )

    return {
        "seller": seller,
        "colleague": colleague,
        "membership": membership,
        "travertine": travertine,
        "granite": granite,
        "recent_trade": recent,
    }


ALL_TIME = DateRange()


# --- 4. totals -------------------------------------------------------------------


@pytest.mark.django_db
def test_the_sales_summary_totals_every_trade(books):
    summary = reports.sales_summary(books["seller"], ALL_TIME)
    assert summary["total"] == Decimal("90000000.00")
    assert summary["quantity_sqm"] == Decimal("70.000")
    assert summary["trade_count"] == 3


@pytest.mark.django_db
def test_a_date_window_excludes_older_trades(books):
    window = DateRange(date_from=timezone.localdate() - timedelta(days=7))
    summary = reports.sales_summary(books["seller"], window)
    assert summary["total"] == Decimal("80000000.00")
    assert summary["trade_count"] == 2


@pytest.mark.django_db
def test_a_trade_recorded_today_is_inside_a_window_ending_today(books):
    """Guards the date-vs-datetime off-by-one that drops the closing day."""
    today = timezone.localdate()
    summary = reports.sales_summary(books["seller"], DateRange(date_from=today, date_to=today))
    assert summary["trade_count"] == 2


@pytest.mark.django_db
def test_another_businesss_sales_are_never_counted(books):
    other = make_business(name="سنگ غریبه", owner_phone="09231110009")
    record_direct_sale(
        seller_business=other,
        membership=owner_membership(other),
        item=make_item(other, lot_code="OT-1"),
        quantity_sqm=Decimal("5"),
        unit_price=Decimal("9000000"),
        customer_name="کسی",
    )
    assert reports.sales_summary(books["seller"], ALL_TIME)["total"] == Decimal("90000000.00")


# --- 1/2/3. groupings --------------------------------------------------------------


@pytest.mark.django_db
def test_sales_by_colleague_separates_the_walk_in_customer(books):
    rows = reports.sales_by_colleague(books["seller"], ALL_TIME)
    by_name = {row["name"]: row for row in rows}

    assert by_name["سنگ همکار الف"]["total"] == Decimal("50000000.00")
    assert by_name["سنگ همکار الف"]["is_colleague"] is True
    assert by_name["آقای رضایی"]["total"] == Decimal("40000000.00")
    assert by_name["آقای رضایی"]["is_colleague"] is False


@pytest.mark.django_db
def test_sales_by_stone_type_sums_per_type(books):
    rows = {row["name"]: row for row in reports.sales_by_stone_type(books["seller"], ALL_TIME)}
    assert rows["تراورتن"]["total"] == Decimal("50000000.00")
    assert rows["تراورتن"]["quantity"] == Decimal("50.000")
    assert rows["گرانیت"]["total"] == Decimal("40000000.00")


@pytest.mark.django_db
def test_grouping_uses_the_trade_snapshot_not_the_live_product(books):
    """Reclassifying a product must not move historical revenue."""
    product = books["travertine"].product
    product.stone_type = "چیز دیگر"
    product.save(update_fields=["stone_type"])

    rows = {row["name"]: row for row in reports.sales_by_stone_type(books["seller"], ALL_TIME)}
    assert rows["تراورتن"]["total"] == Decimal("50000000.00")
    assert "چیز دیگر" not in rows


@pytest.mark.django_db
def test_sales_by_product_totals_match_the_grand_total(books):
    rows = reports.sales_by_product(books["seller"], ALL_TIME)
    assert sum(row["total"] for row in rows) == Decimal("90000000.00")


# --- 5/6. debtors and creditors ----------------------------------------------------


@pytest.mark.django_db
def test_debtors_and_creditors_land_on_the_right_side(books):
    other = make_business(name="سنگ بستانکار", owner_phone="09231110003")
    post_manual_entry(
        business=books["seller"],
        counterparty=other,
        membership=books["membership"],
        entry_type=LedgerEntry.Type.PAYMENT_RECEIVED,
        amount=Decimal("7000000"),
    )

    def amounts(state):
        return {
            row["colleague"].name: row["balance"]["amount"]
            for row in reports.balances(books["seller"], state=state)
        }

    debtors = amounts("debtor")
    creditors = amounts("creditor")

    # Two colleague sales posted 50,000,000 to the colleague's account.
    assert debtors == {"سنگ همکار الف": Decimal("50000000.00")}
    assert creditors == {"سنگ بستانکار": Decimal("7000000.00")}


# --- money movement -----------------------------------------------------------------


@pytest.mark.django_db
def test_received_and_paid_are_reported_separately(books):
    post_manual_entry(
        business=books["seller"],
        counterparty=books["colleague"],
        membership=books["membership"],
        entry_type=LedgerEntry.Type.PAYMENT_RECEIVED,
        amount=Decimal("20000000"),
    )
    post_manual_entry(
        business=books["seller"],
        counterparty=books["colleague"],
        membership=books["membership"],
        entry_type=LedgerEntry.Type.PAYMENT_MADE,
        amount=Decimal("5000000"),
    )
    money = reports.money_movement(books["seller"], ALL_TIME)
    assert money["received"] == Decimal("20000000.00")
    assert money["paid"] == Decimal("5000000.00")


@pytest.mark.django_db
def test_a_reversed_receipt_is_not_reported_as_money_that_arrived(books):
    entry = post_manual_entry(
        business=books["seller"],
        counterparty=books["colleague"],
        membership=books["membership"],
        entry_type=LedgerEntry.Type.PAYMENT_RECEIVED,
        amount=Decimal("20000000"),
    )
    reverse_entry(entry=entry, membership=books["membership"])
    assert reports.money_movement(books["seller"], ALL_TIME)["received"] == Decimal("0.00")


# --- 8. invoices ----------------------------------------------------------------------


@pytest.mark.django_db
def test_cancelled_invoices_are_counted_but_not_totalled(books):
    line = {
        "product_name": "تراورتن",
        "quantity": Decimal("10"),
        "unit_price": Decimal("1000000"),
        "item": None,
    }
    create_manual_invoice(
        business=books["seller"],
        membership=books["membership"],
        lines=[line],
        customer_name="مشتری نقدی",
    )
    doomed = create_manual_invoice(
        business=books["seller"],
        membership=books["membership"],
        lines=[line],
        customer_name="مشتری نقدی",
    )
    cancel_invoice(invoice=doomed, membership=books["membership"])

    # The three fixture sales each produced an invoice automatically
    # (90,000,000), plus the two typed here at 10,000,000 each, one of which is
    # cancelled and so excluded from the total but still counted.
    summary = reports.invoice_summary(books["seller"], ALL_TIME)
    assert summary["total"] == Decimal("100000000.00")
    assert summary["total_count"] == 5
    assert summary["cancelled_count"] == 1


# --- 10. freshness --------------------------------------------------------------------


@pytest.mark.django_db
def test_only_stale_products_are_listed_for_a_stock_check(books):
    expire_stock(books["travertine"])
    codes = {item.lot_code for item in reports.stock_needing_confirmation(books["seller"])}
    assert codes == {"TR-1"}


@pytest.mark.django_db
def test_an_unavailable_product_is_not_nagged_about(books):
    """Nothing to confirm: the seller has already said they are out."""
    from apps.inventory.models import InventoryLot

    expire_stock(books["travertine"])
    books["travertine"].availability_status = InventoryLot.Availability.UNAVAILABLE
    books["travertine"].save()
    assert not reports.stock_needing_confirmation(books["seller"]).exists()


@pytest.mark.django_db
def test_expired_prices_are_listed(books):
    from apps.core.testing import expire_price

    expire_price(books["travertine"], "b2b")
    rows = list(reports.prices_needing_confirmation(books["seller"]))
    assert [row.lot.lot_code for row in rows] == ["TR-1"]


# --- pages -------------------------------------------------------------------------


def _login(client, business):
    client.force_login(business.memberships.get(role="owner").user)
    session = client.session
    session["current_business_id"] = str(business.id)
    session.save()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "key",
    [
        "summary",
        "by_colleague",
        "by_stone_type",
        "by_product",
        "debtors",
        "creditors",
        "invoices",
        "aging",
        "stock_check",
        "price_check",
    ],
)
def test_every_report_renders(client, books, key):
    _login(client, books["seller"])
    response = client.get(reverse("reporting:report", kwargs={"key": key}))
    assert response.status_code == 200


@pytest.mark.django_db
def test_an_unknown_report_key_falls_back_to_the_summary(client, books):
    _login(client, books["seller"])
    response = client.get(reverse("reporting:report", kwargs={"key": "nonsense"}))
    assert response.status_code == 200
    assert response.context["active"] == "summary"


@pytest.mark.django_db
def test_the_print_view_renders_the_same_numbers(client, books):
    _login(client, books["seller"])
    body = client.get(reverse("reporting:report", kwargs={"key": "summary"}) + "?print=1").content.decode()
    assert "90,000,000" in body


@pytest.mark.django_db
def test_reports_require_the_ledger_capability(client, books):
    from apps.businesses.models import BusinessMembership
    from apps.core.testing import make_user

    books["seller"].seat_limit = 5
    books["seller"].save(update_fields=["seat_limit"])
    viewer = BusinessMembership.objects.create(
        user=make_user("09231119999"),
        business=books["seller"],
        role=BusinessMembership.Role.VIEWER,
        status=BusinessMembership.Status.ACTIVE,
    )
    assert not viewer.has_capability("ledger.view")

    client.force_login(viewer.user)
    session = client.session
    session["current_business_id"] = str(books["seller"].id)
    session.save()

    response = client.get(reverse("reporting:index"))
    assert response.status_code == 302
