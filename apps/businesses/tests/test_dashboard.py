"""The dashboard: correct numbers, tenant-scoped, capability-gated, cheap."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.accounting.services import post_entry
from apps.businesses.models import Business, BusinessMembership
from apps.businesses.services import create_business_for_owner
from apps.contacts.services import archive_contact, create_contact
from apps.core.testing import expire_stock, make_item, make_product
from apps.inquiries.models import Inquiry
from apps.pricing.services import ensure_default_tiers
from apps.trading.models import PurchaseRequest

User = get_user_model()

DASHBOARD = "/app/"


@pytest.fixture
def shop(db):
    """One business with books, lots and pending work, plus a colleague."""
    ensure_default_tiers()
    owner = User.objects.create_user(phone="09127770001", full_name="مالک اصلی")
    colleague_owner = User.objects.create_user(phone="09127770002", full_name="مالک همکار")

    business = create_business_for_owner(owner=owner, name="سنگ اصلی", city="محلات")
    colleague = create_business_for_owner(owner=colleague_owner, name="سنگ همکار", city="تهران")
    membership = BusinessMembership.objects.get(user=owner, business=business)
    colleague_m = BusinessMembership.objects.get(user=colleague_owner, business=colleague)

    product = make_product(business, commercial_name="مرمریت اصلی", stone_type="مرمریت")
    colleague_product = make_product(colleague, commercial_name="تراورتن همکار")

    priced = make_item(business, product=product, lot_code="OK-1", b2b="1000000", b2c="2000000")
    unpriced = make_item(business, product=product, lot_code="NOPRICE-1")
    stale = make_item(business, product=product, lot_code="STALE-1", b2b="900000", b2c="1900000")
    expire_stock(stale)

    colleague_lot = make_item(
        colleague, product=colleague_product, lot_code="COL-1", b2b="1500000", b2c="2500000"
    )
    colleague_private = make_item(
        colleague, product=colleague_product, lot_code="COL-PRIV", is_visible=False
    )

    debtor = create_contact(business=business, membership=membership, display_name="بدهکار بزرگ")
    creditor = create_contact(business=business, membership=membership, display_name="بستانکار بزرگ")
    post_entry(
        business=business,
        contact=debtor,
        membership=membership,
        entry_type="sale",
        amount=Decimal("5000000"),
        description="فروش",
    )
    post_entry(
        business=business,
        contact=creditor,
        membership=membership,
        entry_type="payment_received",
        amount=Decimal("2000000"),
        description="دریافت",
    )

    Inquiry.objects.create(
        business=business, lot=priced, name="مشتری تازه", phone="09121110000"
    )
    Inquiry.objects.create(
        business=business,
        lot=priced,
        name="مشتری پیگیری‌شده",
        phone="09121110001",
        status=Inquiry.Status.CONTACTED,
    )
    # A colleague asking to buy from us: our task until we answer it.
    incoming = PurchaseRequest.objects.create(
        item=priced,
        seller_business=business,
        buyer_business=colleague,
        requested_qty_sqm=Decimal("100"),
        proposed_unit_price=Decimal("1200000"),
    )

    return {
        "owner": owner,
        "business": business,
        "membership": membership,
        "colleague": colleague,
        "colleague_m": colleague_m,
        "colleague_lot": colleague_lot,
        "colleague_private": colleague_private,
        "debtor": debtor,
        "creditor": creditor,
        "unpriced": unpriced,
        "stale": stale,
        "priced": priced,
        "incoming_request": incoming,
    }


def _login(client, user, business) -> None:
    client.force_login(user)
    session = client.session
    session["current_business_id"] = str(business.id)
    session.save()


def _dashboard(client, shop):
    _login(client, shop["owner"], shop["business"])
    response = client.get(DASHBOARD)
    assert response.status_code == 200
    return response


# --- financial sections -----------------------------------------------------


def test_the_financial_summary_is_the_ledger_summary(client, shop):
    finance = _dashboard(client, shop).context["finance"]
    assert finance["receivable_total"] == Decimal("5000000.00")
    assert finance["payable_total"] == Decimal("2000000.00")
    assert finance["net_balance"] == Decimal("3000000.00")
    assert finance["net"]["label"] == "بدهکار"


def test_top_debtors_and_creditors_are_labeled_and_linked(client, shop):
    context = _dashboard(client, shop).context
    assert [row["contact"].id for row in context["top_debtors"]] == [shop["debtor"].id]
    assert context["top_debtors"][0]["balance"]["label"] == "بدهکار"
    assert [row["contact"].id for row in context["top_creditors"]] == [shop["creditor"].id]
    assert context["top_creditors"][0]["balance"]["label"] == "بستانکار"

    body = _dashboard(client, shop).content.decode()
    assert reverse("accounting:statement", kwargs={"contact_id": shop["debtor"].id}) in body
    assert "بستانکار" in body


def test_an_archived_debtor_is_still_listed_and_marked(client, shop):
    archive_contact(contact=shop["debtor"], membership=shop["membership"])

    response = _dashboard(client, shop)
    assert [row["contact"].id for row in response.context["top_debtors"]] == [shop["debtor"].id]
    assert "بایگانی‌شده" in response.content.decode()


def test_a_member_without_ledger_view_gets_no_financial_data(client, shop):
    viewer = User.objects.create_user(phone="09127770003", full_name="بازدیدکننده")
    BusinessMembership.objects.create(
        user=viewer,
        business=shop["business"],
        role=BusinessMembership.Role.VIEWER,
        status=BusinessMembership.Status.ACTIVE,
    )
    _login(client, viewer, shop["business"])
    response = client.get(DASHBOARD)

    assert response.status_code == 200
    # Absent from the context, not merely hidden in the HTML.
    assert response.context["can_view_ledger"] is False
    assert response.context["finance"] is None
    assert response.context["top_debtors"] == []
    assert response.context["top_creditors"] == []

    body = response.content.decode().replace(",", "")
    assert "5000000" not in body
    assert "خلاصه مالی" not in body
    # The rest of the dashboard still renders for them.
    assert "محصولات نیازمند رسیدگی" in body


def test_the_financial_sections_stay_inside_their_own_tenant(client, shop):
    _login(client, shop["colleague"].memberships.first().user, shop["colleague"])
    response = client.get(DASHBOARD)

    assert response.context["finance"]["receivable_total"] == Decimal("0.00")
    assert response.context["top_debtors"] == []
    assert "بدهکار بزرگ" not in response.content.decode()


# --- lots needing attention -------------------------------------------------


def test_lots_needing_attention_list_both_reasons(client, shop):
    context = _dashboard(client, shop).context
    rows = {row["lot"].lot_code: row["reasons"] for row in context["attention_lots"]}

    assert set(rows) == {"NOPRICE-1", "STALE-1"}
    assert rows["NOPRICE-1"] == ["بدون قیمت — قابل فروش نیست"]
    assert rows["STALE-1"] == ["نیاز به تأیید موجودی"]
    assert context["lot_totals"] == {
        "active": 3,
        "needs_confirmation": 1,
        "stale_price": 0,
        "no_price": 1,
    }


def test_an_item_that_is_both_unconfirmed_and_unpriced_appears_once_with_both_reasons(
    client, shop
):
    expire_stock(shop["unpriced"])

    rows = [
        row for row in _dashboard(client, shop).context["attention_lots"]
        if row["lot"].lot_code == "NOPRICE-1"
    ]
    assert len(rows) == 1
    assert rows[0]["reasons"] == ["نیاز به تأیید موجودی", "بدون قیمت — قابل فروش نیست"]


def test_attention_lots_never_include_another_businesses_lots(client, shop):
    codes = {row["lot"].lot_code for row in _dashboard(client, shop).context["attention_lots"]}
    assert "COL-PRIV" not in codes
    assert "COL-1" not in codes


# --- colleague lots ---------------------------------------------------------


def test_colleague_lots_come_through_the_marketplace_gate(client, shop):
    codes = [lot.lot_code for lot in _dashboard(client, shop).context["colleague_lots"]]
    assert codes == ["COL-1"]
    # Never the viewer's own lots, never a private lot.
    assert "OK-1" not in codes
    assert "COL-PRIV" not in codes


def test_a_suspended_colleagues_lots_leave_the_dashboard(client, shop):
    colleague = shop["colleague"]
    colleague.status = Business.Status.SUSPENDED
    colleague.save(update_fields=["status"])

    response = _dashboard(client, shop)
    assert list(response.context["colleague_lots"]) == []
    assert "تراورتن همکار" not in response.content.decode()


def test_a_suspended_viewer_sees_no_colleague_lots(client, shop):
    business = shop["business"]
    business.status = Business.Status.SUSPENDED
    business.save(update_fields=["status"])

    assert list(_dashboard(client, shop).context["colleague_lots"]) == []


# --- pending work -----------------------------------------------------------


def test_pending_work_counts_only_unanswered_items(client, shop):
    context = _dashboard(client, shop).context
    # The «تماس گرفته‌شده» inquiry is already being handled.
    assert context["unanswered_inquiry_count"] == 1
    assert [i.name for i in context["unanswered_inquiries"]] == ["مشتری تازه"]
    assert context["open_request_count"] == 1
    assert [r.id for r in context["open_requests"]] == [shop["incoming_request"].id]


def test_an_accepted_request_stays_on_the_list_until_the_sale_is_finalized(client, shop):
    """An agreement nobody finalized is unfinished work, not a closed item."""
    request_ = shop["incoming_request"]
    request_.status = PurchaseRequest.Status.ACCEPTED
    request_.final_unit_price = Decimal("1200000")
    request_.save(update_fields=["status", "final_unit_price"])

    context = _dashboard(client, shop).context
    assert context["open_request_count"] == 1
    assert context["awaiting_finalize_count"] == 1


def test_a_rejected_request_leaves_the_pending_list(client, shop):
    request_ = shop["incoming_request"]
    request_.status = PurchaseRequest.Status.REJECTED
    request_.save(update_fields=["status"])

    assert _dashboard(client, shop).context["open_request_count"] == 0


def test_pending_work_is_tenant_scoped(client, shop):
    _login(client, shop["colleague"].memberships.first().user, shop["colleague"])
    context = client.get(DASHBOARD).context

    assert context["unanswered_inquiry_count"] == 0
    # The request the colleague *sent* is somebody else's decision, not its own task.
    assert context["open_request_count"] == 0


# --- empty state and cost ---------------------------------------------------


def test_a_brand_new_business_gets_a_coherent_empty_dashboard(client, db):
    owner = User.objects.create_user(phone="09127770009", full_name="تازه‌وارد")
    business = create_business_for_owner(owner=owner, name="سنگ تازه", city="یزد")
    _login(client, owner, business)

    response = client.get(DASHBOARD)
    assert response.status_code == 200
    body = response.content.decode()
    assert response.context["finance"]["contact_count"] == 0
    assert response.context["attention_lots"] == []
    assert list(response.context["colleague_lots"]) == []
    assert "هنوز محصولی ثبت نکرده‌اید" in body
    assert "هنوز مخاطبی ندارید" in body
    assert "استعلام بی‌پاسخی ندارید" in body


def test_the_dashboard_query_count_stays_bounded(client, shop, django_assert_max_num_queries):
    _login(client, shop["owner"], shop["business"])
    # A fully-populated dashboard costs 14: session + user + the two membership
    # lookups in the middleware, then exactly one query per piece of data —
    # summary, top debtors, top creditors, lot totals, lots needing attention,
    # colleague lots, and a count plus a page for each of the two pending lists.
    # The bound is that 14 plus a little slack for shell/session changes; it is
    # flat in the number of rows, so an N+1 anywhere on the page (five debtors,
    # eight lots, six colleague lots, ten pending rows) breaks it immediately.
    with django_assert_max_num_queries(16):
        assert client.get(DASHBOARD).status_code == 200
