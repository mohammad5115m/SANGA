from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.accounting.models import LedgerEntry
from apps.accounting.reports import business_aging, contact_aging
from apps.accounting.selectors import (
    business_financial_summary,
    contact_balances,
    contact_statement,
    statement_totals,
)
from apps.accounting.services import post_entry, reverse_entry
from apps.businesses.models import BusinessMembership
from apps.businesses.services import create_business_for_owner
from apps.contacts.services import archive_contact, create_contact

User = get_user_model()


@pytest.fixture
def books(db):
    owner_a = User.objects.create_user(phone="09120000401", full_name="مالک الف")
    owner_b = User.objects.create_user(phone="09120000402", full_name="مالک ب")
    owner_c = User.objects.create_user(phone="09120000403", full_name="مالک ج")

    biz_a = create_business_for_owner(owner=owner_a, name="سنگ الف", city="محلات")
    biz_b = create_business_for_owner(owner=owner_b, name="سنگ ب", city="تهران")
    # A business with no contacts at all, for the empty-state assertions.
    biz_c = create_business_for_owner(owner=owner_c, name="سنگ ج", city="اصفهان")

    m_a = BusinessMembership.objects.get(user=owner_a, business=biz_a)
    m_b = BusinessMembership.objects.get(user=owner_b, business=biz_b)
    m_c = BusinessMembership.objects.get(user=owner_c, business=biz_c)

    return {
        "owner_a": owner_a,
        "biz_a": biz_a,
        "biz_b": biz_b,
        "biz_c": biz_c,
        "m_a": m_a,
        "m_b": m_b,
        "m_c": m_c,
        "contact_a": create_contact(
            business=biz_a, membership=m_a, display_name="مشتری الف"
        ),
        "contact_a2": create_contact(
            business=biz_a, membership=m_a, display_name="مشتری دوم"
        ),
        "contact_b": create_contact(
            business=biz_b, membership=m_b, display_name="مشتری ب"
        ),
    }


def _post(books, entry_type, amount, *, days_ago=0, contact=None, business=None, membership=None):
    return post_entry(
        business=business or books["biz_a"],
        contact=contact or books["contact_a"],
        membership=membership or books["m_a"],
        entry_type=entry_type,
        amount=Decimal(amount),
        description="بابت آزمون",
        occurred_on=timezone.localdate() - timedelta(days=days_ago),
    )


def _sale(books, amount, *, days_ago=0, contact=None):
    return _post(books, LedgerEntry.Type.SALE, amount, days_ago=days_ago, contact=contact)


def _payment(books, amount, *, days_ago=0, contact=None):
    return _post(
        books, LedgerEntry.Type.PAYMENT_RECEIVED, amount, days_ago=days_ago, contact=contact
    )


# --- debit / credit column classification -----------------------------------


@pytest.mark.parametrize(
    "entry_type,expect_debit",
    [
        (LedgerEntry.Type.SALE, True),
        (LedgerEntry.Type.PAYMENT_MADE, True),
        (LedgerEntry.Type.ADJUST_DEBIT, True),
        (LedgerEntry.Type.PURCHASE, False),
        (LedgerEntry.Type.PAYMENT_RECEIVED, False),
        (LedgerEntry.Type.ADJUST_CREDIT, False),
    ],
)
def test_every_entry_belongs_to_exactly_one_column(books, entry_type, expect_debit):
    entry = _post(books, entry_type, "1000")
    assert entry.is_debit is expect_debit
    assert entry.is_credit is not expect_debit


def test_a_reversal_sits_in_the_opposite_column_of_its_original(books):
    sale = _sale(books, "1000")
    reversal = reverse_entry(entry=sale, membership=books["m_a"])
    assert sale.is_debit and reversal.is_credit


# --- statement totals -------------------------------------------------------


def test_statement_totals_sum_each_column_separately(books):
    _sale(books, "1000000", days_ago=3)
    _sale(books, "500000", days_ago=2)
    _payment(books, "400000", days_ago=1)

    totals = statement_totals(contact_statement(books["biz_a"], books["contact_a"]))
    assert totals["debit"] == Decimal("1500000.00")
    assert totals["credit"] == Decimal("400000.00")
    assert totals["row_count"] == 3
    assert totals["closing"] == Decimal("1100000.00")
    assert totals["closing_balance"]["state"] == "debtor"
    assert totals["closing_balance"]["amount"] == Decimal("1100000.00")


def test_statement_totals_follow_the_active_filters(books):
    _sale(books, "1000000", days_ago=10)
    _sale(books, "500000", days_ago=1)
    _payment(books, "200000", days_ago=1)

    entries = contact_statement(
        books["biz_a"],
        books["contact_a"],
        date_from=timezone.localdate() - timedelta(days=2),
    )
    totals = statement_totals(entries)
    assert totals["debit"] == Decimal("500000.00")
    assert totals["credit"] == Decimal("200000.00")
    assert totals["row_count"] == 2

    only_sales = statement_totals(
        contact_statement(
            books["biz_a"], books["contact_a"], entry_type=LedgerEntry.Type.SALE
        )
    )
    assert only_sales["debit"] == Decimal("1500000.00")
    assert only_sales["credit"] == Decimal("0.00")


def test_statement_totals_of_an_empty_period_claim_no_closing_balance(books):
    _sale(books, "1000000", days_ago=100)

    totals = statement_totals(
        contact_statement(
            books["biz_a"],
            books["contact_a"],
            date_from=timezone.localdate() - timedelta(days=1),
        )
    )
    assert totals["row_count"] == 0
    assert totals["debit"] == Decimal("0.00")
    assert totals["credit"] == Decimal("0.00")
    assert totals["closing"] is None
    assert totals["closing_balance"] is None


def test_closing_balance_equals_the_last_visible_running_balance(books):
    _sale(books, "700000", days_ago=5)
    last = _payment(books, "200000", days_ago=4)

    totals = statement_totals(contact_statement(books["biz_a"], books["contact_a"]))
    assert totals["closing"] == last.balance_after


# --- aging: FIFO allocation -------------------------------------------------


@pytest.mark.parametrize(
    "days_ago,bucket",
    [
        (0, "current"),
        (30, "current"),
        (31, "days_31_60"),
        (60, "days_31_60"),
        (61, "days_61_90"),
        (90, "days_61_90"),
        (91, "over_90"),
        (400, "over_90"),
    ],
)
def test_a_single_unpaid_debit_lands_in_the_right_bucket(books, days_ago, bucket):
    _sale(books, "1000000", days_ago=days_ago)

    aging = contact_aging(books["biz_a"], books["contact_a"])
    assert getattr(aging, bucket) == Decimal("1000000.00")
    assert aging.total == Decimal("1000000.00")
    assert aging.unapplied_credit == Decimal("0.00")


def test_a_payment_settles_the_oldest_debit_first(books):
    _sale(books, "1000000", days_ago=120)
    _sale(books, "2000000", days_ago=5)
    _payment(books, "1000000", days_ago=1)

    aging = contact_aging(books["biz_a"], books["contact_a"])
    assert aging.over_90 == Decimal("0.00")
    assert aging.current == Decimal("2000000.00")
    assert aging.total == Decimal("2000000.00")


def test_a_partial_payment_reduces_the_oldest_debit_rather_than_spreading(books):
    _sale(books, "1000000", days_ago=120)
    _sale(books, "2000000", days_ago=5)
    _payment(books, "600000", days_ago=1)

    aging = contact_aging(books["biz_a"], books["contact_a"])
    # The whole payment lands on the oldest debt; the recent one is untouched.
    assert aging.over_90 == Decimal("400000.00")
    assert aging.current == Decimal("2000000.00")
    assert aging.total == Decimal("2400000.00")


def test_a_payment_larger_than_the_oldest_debit_spills_to_the_next_one(books):
    _sale(books, "1000000", days_ago=120)
    _sale(books, "2000000", days_ago=45)
    _sale(books, "3000000", days_ago=2)
    _payment(books, "2500000", days_ago=1)

    aging = contact_aging(books["biz_a"], books["contact_a"])
    assert aging.over_90 == Decimal("0.00")
    assert aging.days_31_60 == Decimal("500000.00")
    assert aging.current == Decimal("3000000.00")


def test_allocation_follows_the_business_date_not_the_posting_order(books):
    """A backdated invoice posted last is still the oldest debt, so it absorbs the
    payment first.
    """
    _sale(books, "1000000", days_ago=5)
    _sale(books, "1000000", days_ago=200)
    _payment(books, "1000000")

    aging = contact_aging(books["biz_a"], books["contact_a"])
    assert aging.over_90 == Decimal("0.00")
    assert aging.current == Decimal("1000000.00")


def test_a_credit_balance_produces_no_aging_rows(books):
    _sale(books, "300000", days_ago=100)
    _payment(books, "500000", days_ago=1)

    aging = contact_aging(books["biz_a"], books["contact_a"])
    assert aging.total == Decimal("0.00")
    assert aging.has_outstanding is False
    assert all(row["amount"] == Decimal("0.00") for row in aging.rows)
    assert aging.unapplied_credit == Decimal("200000.00")


def test_an_account_without_entries_has_no_aging(books):
    aging = contact_aging(books["biz_a"], books["contact_a"])
    assert aging.total == Decimal("0.00")
    assert aging.unapplied_credit == Decimal("0.00")


def test_a_reversed_debit_is_not_aged(books):
    sale = _sale(books, "1000000", days_ago=120)
    reverse_entry(entry=sale, membership=books["m_a"])

    aging = contact_aging(books["biz_a"], books["contact_a"])
    assert aging.total == Decimal("0.00")
    assert aging.unapplied_credit == Decimal("0.00")


def test_reversing_a_payment_puts_the_old_debt_back_in_its_bucket(books):
    _sale(books, "1000000", days_ago=120)
    payment = _payment(books, "1000000", days_ago=1)
    assert contact_aging(books["biz_a"], books["contact_a"]).total == Decimal("0.00")

    reverse_entry(entry=payment, membership=books["m_a"])

    aging = contact_aging(books["biz_a"], books["contact_a"])
    assert aging.over_90 == Decimal("1000000.00")
    assert aging.total == Decimal("1000000.00")


def test_aging_is_scoped_to_the_business(books):
    _post(
        books,
        LedgerEntry.Type.SALE,
        "5000000",
        days_ago=120,
        contact=books["contact_b"],
        business=books["biz_b"],
        membership=books["m_b"],
    )
    assert business_aging(books["biz_a"])["total"].total == Decimal("0.00")
    assert business_aging(books["biz_b"])["total"].total == Decimal("5000000.00")


def test_business_aging_lists_debtors_largest_first_and_skips_settled_ones(books):
    _sale(books, "1000000", days_ago=120)
    _sale(books, "4000000", days_ago=10, contact=books["contact_a2"])

    report = business_aging(books["biz_a"])
    assert [row["contact"].id for row in report["rows"]] == [
        books["contact_a2"].id,
        books["contact_a"].id,
    ]
    assert report["total"].over_90 == Decimal("1000000.00")
    assert report["total"].current == Decimal("4000000.00")
    assert report["total"].total == Decimal("5000000.00")


def test_a_creditor_contact_is_left_out_of_the_aging_rows(books):
    _sale(books, "1000000", days_ago=120)
    _payment(books, "300000", contact=books["contact_a2"])

    report = business_aging(books["biz_a"])
    assert [row["contact"].id for row in report["rows"]] == [books["contact_a"].id]
    assert report["total"].unapplied_credit == Decimal("300000.00")


# --- business-wide summary --------------------------------------------------


def test_summary_splits_receivables_from_payables(books):
    _sale(books, "1000000")
    _payment(books, "250000", contact=books["contact_a2"])

    summary = business_financial_summary(books["biz_a"])
    assert summary["receivable_total"] == Decimal("1000000.00")
    assert summary["payable_total"] == Decimal("250000.00")
    assert summary["net_balance"] == Decimal("750000.00")
    assert summary["net"]["state"] == "debtor"
    assert summary["net"]["amount"] == Decimal("750000.00")
    assert summary["debtor_count"] == 1
    assert summary["creditor_count"] == 1
    assert summary["settled_count"] == 0
    assert summary["contact_count"] == 2


def test_summary_reports_payables_as_a_positive_magnitude(books):
    _payment(books, "900000")

    summary = business_financial_summary(books["biz_a"])
    assert summary["payable_total"] == Decimal("900000.00")
    assert summary["net_balance"] == Decimal("-900000.00")
    assert summary["net"]["state"] == "creditor"
    assert summary["net"]["amount"] == Decimal("900000.00")


def test_summary_of_a_business_without_entries_is_all_zero(books):
    summary = business_financial_summary(books["biz_a"])
    assert summary["receivable_total"] == Decimal("0.00")
    assert summary["payable_total"] == Decimal("0.00")
    assert summary["net_balance"] == Decimal("0.00")
    assert summary["debtor_count"] == 0
    assert summary["settled_count"] == 2


def test_summary_of_a_business_without_contacts_is_all_zero(books):
    summary = business_financial_summary(books["biz_c"])
    assert summary["receivable_total"] == Decimal("0.00")
    assert summary["payable_total"] == Decimal("0.00")
    assert summary["net_balance"] == Decimal("0.00")
    assert summary["contact_count"] == 0


def test_summary_ignores_another_businesses_ledger(books):
    _sale(books, "1000000")
    _post(
        books,
        LedgerEntry.Type.SALE,
        "9999999",
        contact=books["contact_b"],
        business=books["biz_b"],
        membership=books["m_b"],
    )

    summary = business_financial_summary(books["biz_a"])
    assert summary["receivable_total"] == Decimal("1000000.00")
    assert summary["contact_count"] == 2


# --- archived contacts must not hide money ----------------------------------


def test_archiving_a_debtor_keeps_the_debt_in_every_report(books):
    """Archiving is housekeeping, not settlement: the money stays on the books."""
    _sale(books, "1000000", days_ago=120)
    archive_contact(contact=books["contact_a"], membership=books["m_a"])

    summary = business_financial_summary(books["biz_a"])
    assert summary["receivable_total"] == Decimal("1000000.00")
    assert summary["debtor_count"] == 1

    rows = list(contact_balances(books["biz_a"]))
    assert books["contact_a"].id in [c.id for c in rows]
    assert [c.is_active for c in rows if c.id == books["contact_a"].id] == [False]

    report = business_aging(books["biz_a"])
    assert [row["contact"].id for row in report["rows"]] == [books["contact_a"].id]
    assert report["total"].over_90 == Decimal("1000000.00")


def test_archiving_a_creditor_keeps_the_payable_on_the_books(books):
    _payment(books, "800000")
    archive_contact(contact=books["contact_a"], membership=books["m_a"])

    summary = business_financial_summary(books["biz_a"])
    assert summary["payable_total"] == Decimal("800000.00")
    assert summary["creditor_count"] == 1
    assert business_aging(books["biz_a"])["total"].unapplied_credit == Decimal("800000.00")


def test_an_archived_settled_contact_leaves_the_reports(books):
    _sale(books, "500000", days_ago=10)
    _payment(books, "500000", days_ago=1)
    archive_contact(contact=books["contact_a"], membership=books["m_a"])

    ids = [c.id for c in contact_balances(books["biz_a"])]
    assert books["contact_a"].id not in ids
    # The still-active contact without entries is not affected.
    assert books["contact_a2"].id in ids

    summary = business_financial_summary(books["biz_a"])
    assert summary["contact_count"] == 1
    assert summary["receivable_total"] == Decimal("0.00")
    assert [row["contact"].id for row in business_aging(books["biz_a"])["rows"]] == []


def test_an_archived_contact_without_entries_is_never_reported(books):
    archive_contact(contact=books["contact_a"], membership=books["m_a"])

    assert [c.id for c in contact_balances(books["biz_a"])] == [books["contact_a2"].id]
    assert business_financial_summary(books["biz_a"])["contact_count"] == 1


def test_the_summary_reconciles_with_the_balances_even_with_archived_rows(books):
    _sale(books, "1000000", days_ago=120)
    _payment(books, "250000", contact=books["contact_a2"])
    archive_contact(contact=books["contact_a"], membership=books["m_a"])
    archive_contact(contact=books["contact_a2"], membership=books["m_a"])

    summary = business_financial_summary(books["biz_a"])
    rows = list(contact_balances(books["biz_a"]))
    assert sum(c.balance for c in rows) == summary["net_balance"]
    assert sum(c.balance for c in rows if c.balance > 0) == summary["receivable_total"]
    assert -sum(c.balance for c in rows if c.balance < 0) == summary["payable_total"]

    report = business_aging(books["biz_a"])
    assert report["total"].total == summary["receivable_total"]
    assert report["total"].unapplied_credit == summary["payable_total"]


def test_archived_rows_stay_inside_their_own_tenant(books):
    _sale(books, "1000000")
    _post(
        books,
        LedgerEntry.Type.SALE,
        "9999999",
        contact=books["contact_b"],
        business=books["biz_b"],
        membership=books["m_b"],
    )
    archive_contact(contact=books["contact_a"], membership=books["m_a"])
    archive_contact(contact=books["contact_b"], membership=books["m_b"])

    a_ids = {c.id for c in contact_balances(books["biz_a"])}
    assert a_ids == {books["contact_a"].id, books["contact_a2"].id}
    assert {c.id for c in contact_balances(books["biz_b"])} == {books["contact_b"].id}
    assert business_financial_summary(books["biz_a"])["receivable_total"] == Decimal("1000000.00")
    assert [row["contact"].id for row in business_aging(books["biz_a"])["rows"]] == [
        books["contact_a"].id
    ]


def test_the_ledger_index_marks_an_archived_debtor(client, books):
    _sale(books, "1000000")
    archive_contact(contact=books["contact_a"], membership=books["m_a"])
    client.force_login(books["owner_a"])

    body = client.get("/app/accounting/").content.decode()
    assert "مشتری الف" in body
    assert "بایگانی‌شده" in body


def test_summary_and_aging_agree_on_receivables_and_payables(books):
    """The FIFO report and the SQL aggregation are independent implementations of
    the same money, so they must land on the same totals.
    """
    _sale(books, "1000000", days_ago=120)
    _payment(books, "400000", days_ago=2)
    _payment(books, "250000", contact=books["contact_a2"])

    summary = business_financial_summary(books["biz_a"])
    report = business_aging(books["biz_a"])
    assert report["total"].total == summary["receivable_total"]
    assert report["total"].unapplied_credit == summary["payable_total"]


# --- ledger index filtering / sorting ---------------------------------------


def test_contact_balances_can_be_filtered_by_accounting_state(books):
    _sale(books, "1000000")
    _payment(books, "250000", contact=books["contact_a2"])

    debtors = contact_balances(books["biz_a"], state="debtor")
    creditors = contact_balances(books["biz_a"], state="creditor")
    settled = contact_balances(books["biz_a"], state="settled")

    assert [c.id for c in debtors] == [books["contact_a"].id]
    assert [c.id for c in creditors] == [books["contact_a2"].id]
    assert list(settled) == []


def test_a_contact_without_entries_counts_as_settled(books):
    assert contact_balances(books["biz_a"], state="settled").count() == 2


def test_contact_balances_can_be_sorted_by_balance(books):
    _sale(books, "1000000")
    _payment(books, "250000", contact=books["contact_a2"])

    by_debt = list(contact_balances(books["biz_a"], sort="debtor"))
    assert [c.id for c in by_debt] == [books["contact_a"].id, books["contact_a2"].id]

    by_credit = list(contact_balances(books["biz_a"], sort="creditor"))
    assert [c.id for c in by_credit] == [books["contact_a2"].id, books["contact_a"].id]


def test_an_unknown_sort_key_falls_back_to_the_name_order(books):
    rows = list(contact_balances(books["biz_a"], sort="'; drop table"))
    assert [c.display_name for c in rows] == ["مشتری الف", "مشتری دوم"]


# --- screens ----------------------------------------------------------------


def test_the_aging_screen_renders_its_buckets(client, books):
    _sale(books, "1000000", days_ago=120)
    client.force_login(books["owner_a"])

    resp = client.get("/app/accounting/aging/")
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "گزارش سنی بدهی" in body
    assert "بیش از ۹۰ روز" in body
    assert "مشتری الف" in body


def test_the_aging_screen_needs_ledger_view(client, books):
    viewer = User.objects.create_user(phone="09120000404", full_name="بازدیدکننده")
    BusinessMembership.objects.create(
        user=viewer,
        business=books["biz_a"],
        role=BusinessMembership.Role.VIEWER,
        status=BusinessMembership.Status.ACTIVE,
    )
    client.force_login(viewer)
    assert client.get("/app/accounting/aging/").status_code == 302


def test_the_ledger_index_shows_the_summary_and_filters_by_state(client, books):
    _sale(books, "1000000")
    _payment(books, "250000", contact=books["contact_a2"])
    client.force_login(books["owner_a"])

    body = client.get("/app/accounting/").content.decode()
    assert "جمع مطالبات" in body
    assert "جمع دیون" in body
    assert "مانده کل" in body

    creditors = client.get("/app/accounting/?state=creditor").content.decode()
    assert "مشتری دوم" in creditors
    assert "مشتری الف" not in creditors


def test_the_statement_screen_uses_debit_and_credit_columns(client, books):
    _sale(books, "1000000", days_ago=3)
    _payment(books, "400000", days_ago=1)
    client.force_login(books["owner_a"])

    body = client.get(
        f"/app/accounting/contacts/{books['contact_a'].id}/"
    ).content.decode()
    assert "بدهکار" in body
    assert "بستانکار" in body
    assert "جمع دوره" in body
    assert "مانده پایان دوره" in body
