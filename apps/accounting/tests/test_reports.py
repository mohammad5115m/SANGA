"""FIFO aging.

Aging exists to answer «چقدر از طلبم قدیمی است؟». Its whole value comes from
applying payments to the *oldest* debt first — spreading them evenly would make
the «بیش از ۹۰ روز» bucket meaningless.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounting.models import LedgerEntry
from apps.accounting.reports import BUCKETS, business_aging, counterparty_aging, live_entries
from apps.accounting.selectors import business_financial_summary
from apps.accounting.services import post_entry, reverse_entry
from apps.core.testing import make_business, owner_membership

SALE = LedgerEntry.Type.SALE
RECEIVED = LedgerEntry.Type.PAYMENT_RECEIVED


@pytest.fixture
def books(db):
    seller = make_business(name="سنگ گزارش", owner_phone="09181110001")
    colleague = make_business(name="سنگ بدهکار", owner_phone="09181110002")
    return {
        "seller": seller,
        "colleague": colleague,
        "membership": owner_membership(seller),
    }


def _post(books, entry_type, amount, days_ago=0, counterparty=None):
    return post_entry(
        business=books["seller"],
        counterparty=counterparty or books["colleague"],
        membership=books["membership"],
        entry_type=entry_type,
        amount=Decimal(amount),
        occurred_on=timezone.localdate() - timedelta(days=days_ago),
        description="دلیل",
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("days_ago", "bucket"),
    [(0, "current"), (30, "current"), (31, "days_31_60"), (61, "days_61_90"), (120, "over_90")],
)
def test_a_debt_lands_in_the_bucket_matching_its_age(books, days_ago, bucket):
    _post(books, SALE, "1000000", days_ago=days_ago)
    aging = counterparty_aging(books["seller"], books["colleague"])
    assert getattr(aging, bucket) == Decimal("1000000.00")
    assert aging.total == Decimal("1000000.00")


@pytest.mark.django_db
def test_a_payment_clears_the_oldest_debt_first(books):
    _post(books, SALE, "1000000", days_ago=120)
    _post(books, SALE, "1000000", days_ago=5)
    _post(books, RECEIVED, "1000000", days_ago=0)

    aging = counterparty_aging(books["seller"], books["colleague"])
    assert aging.over_90 == Decimal("0.00"), "the oldest debt should be the one settled"
    assert aging.current == Decimal("1000000.00")


@pytest.mark.django_db
def test_a_partial_payment_leaves_the_remainder_at_its_own_age(books):
    _post(books, SALE, "1000000", days_ago=120)
    _post(books, RECEIVED, "400000")

    aging = counterparty_aging(books["seller"], books["colleague"])
    assert aging.over_90 == Decimal("600000.00")


@pytest.mark.django_db
def test_a_creditor_account_shows_no_overdue_debt(books):
    """You cannot be overdue on money you do not owe."""
    _post(books, RECEIVED, "2000000")
    aging = counterparty_aging(books["seller"], books["colleague"])
    assert aging.total == Decimal("0.00")
    assert aging.has_outstanding is False
    assert aging.unapplied_credit == Decimal("2000000.00")


@pytest.mark.django_db
def test_a_reversed_entry_and_its_reversal_both_drop_out(books):
    """Otherwise the reversal behaves like a payment against some other debit."""
    entry = _post(books, SALE, "1000000", days_ago=120)
    _post(books, SALE, "500000", days_ago=10)
    reverse_entry(entry=entry, membership=books["membership"])

    live = list(live_entries(books["seller"], books["colleague"]))
    assert len(live) == 1
    assert live[0].amount == Decimal("500000.00")

    aging = counterparty_aging(books["seller"], books["colleague"])
    assert aging.total == Decimal("500000.00")
    assert aging.over_90 == Decimal("0.00")


@pytest.mark.django_db
def test_business_aging_has_one_row_per_indebted_colleague(books):
    third = make_business(name="سنگ سوم", owner_phone="09181110003")
    _post(books, SALE, "1000000", days_ago=100)
    _post(books, SALE, "3000000", days_ago=10, counterparty=third)

    report = business_aging(books["seller"])
    names = [row["counterparty"].name for row in report["rows"]]
    # Sorted by outstanding amount, largest first.
    assert names == ["سنگ سوم", "سنگ بدهکار"]
    assert report["total"].total == Decimal("4000000.00")


@pytest.mark.django_db
def test_a_settled_colleague_is_not_a_row(books):
    _post(books, SALE, "1000000")
    _post(books, RECEIVED, "1000000")
    assert business_aging(books["seller"])["rows"] == []


@pytest.mark.django_db
def test_aging_totals_reconcile_with_the_financial_summary(books):
    third = make_business(name="سنگ سوم", owner_phone="09181110004")
    _post(books, SALE, "5000000", days_ago=40)
    _post(books, RECEIVED, "2000000", counterparty=third)

    report = business_aging(books["seller"])
    summary = business_financial_summary(books["seller"])

    assert report["total"].total == summary["receivable_total"]
    assert report["total"].unapplied_credit == summary["payable_total"]


@pytest.mark.django_db
def test_every_bucket_has_a_label(books):
    _post(books, SALE, "1000000")
    rows = counterparty_aging(books["seller"], books["colleague"]).rows
    assert [row["key"] for row in rows] == [key for key, _ in BUCKETS]
    assert all(row["label"] for row in rows)


@pytest.mark.django_db
def test_a_future_dated_debt_counts_as_current_rather_than_vanishing(books):
    _post(books, SALE, "1000000", days_ago=-10)
    aging = counterparty_aging(books["seller"], books["colleague"])
    assert aging.current == Decimal("1000000.00")
