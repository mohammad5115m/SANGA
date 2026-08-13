"""Ledger posting, balance math, immutability and reversal."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounting.models import LedgerEntry
from apps.accounting.selectors import (
    business_financial_summary,
    counterparty_balances,
    counterparty_statement,
    current_balance,
    describe_balance,
    legacy_entries,
    statement_totals,
)
from apps.accounting.services import (
    LedgerError,
    post_entry,
    post_manual_entry,
    reverse_entry,
)
from apps.businesses.models import BusinessMembership
from apps.core.testing import make_business, make_user, owner_membership

SALE = LedgerEntry.Type.SALE
RECEIVED = LedgerEntry.Type.PAYMENT_RECEIVED
PAID = LedgerEntry.Type.PAYMENT_MADE
ADJUST_DEBIT = LedgerEntry.Type.ADJUST_DEBIT
ADJUST_CREDIT = LedgerEntry.Type.ADJUST_CREDIT


@pytest.fixture
def books(db):
    seller = make_business(name="سنگ دفتر", owner_phone="09171110001")
    colleague = make_business(name="سنگ همکار", owner_phone="09171110002")
    other = make_business(name="سنگ غریبه", owner_phone="09171110003")
    seller.seat_limit = 5
    seller.save(update_fields=["seat_limit"])

    staff = BusinessMembership.objects.create(
        user=make_user("09171110009"),
        business=seller,
        role=BusinessMembership.Role.STAFF,
        status=BusinessMembership.Status.ACTIVE,
    )
    return {
        "seller": seller,
        "colleague": colleague,
        "other": other,
        "seller_m": owner_membership(seller),
        "colleague_m": owner_membership(colleague),
        "other_m": owner_membership(other),
        "staff_m": staff,
    }


def _post(books, entry_type=SALE, amount="1000000", **kwargs):
    params = {
        "business": books["seller"],
        "counterparty": books["colleague"],
        "membership": books["seller_m"],
        "entry_type": entry_type,
        "amount": Decimal(amount),
    }
    params.update(kwargs)
    return post_entry(**params)


# --- direction and balance ----------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("entry_type", "expected_sign"),
    [
        (SALE, 1),
        (PAID, 1),
        (ADJUST_DEBIT, 1),
        (LedgerEntry.Type.PURCHASE, -1),
        (RECEIVED, -1),
        (ADJUST_CREDIT, -1),
    ],
)
def test_each_entry_type_moves_the_balance_one_way(books, entry_type, expected_sign):
    entry = _post(books, entry_type, description="دلیل")
    assert entry.balance_delta == Decimal("1000000.00") * expected_sign
    assert entry.amount == Decimal("1000000.00"), "amount is always a positive magnitude"


@pytest.mark.django_db
def test_the_running_balance_accumulates(books):
    _post(books, SALE, "5000000")
    _post(books, RECEIVED, "2000000")
    assert current_balance(books["seller"], books["colleague"]) == Decimal("3000000.00")


@pytest.mark.django_db
def test_a_selling_business_sees_the_colleague_as_a_debtor(books):
    _post(books, SALE, "5000000")
    described = describe_balance(current_balance(books["seller"], books["colleague"]))
    assert described["state"] == "debtor"
    assert described["label"] == "بدهکار"
    # Never a bare signed number: the label carries the direction.
    assert described["amount"] == Decimal("5000000.00")


@pytest.mark.django_db
def test_an_overpayment_makes_the_business_a_creditor(books):
    _post(books, SALE, "1000000")
    _post(books, RECEIVED, "3000000")
    described = describe_balance(current_balance(books["seller"], books["colleague"]))
    assert described["state"] == "creditor"
    assert described["amount"] == Decimal("2000000.00")


@pytest.mark.django_db
def test_a_settled_account_says_so(books):
    _post(books, SALE, "1000000")
    _post(books, RECEIVED, "1000000")
    assert describe_balance(current_balance(books["seller"], books["colleague"]))["state"] == "settled"


# --- validation ---------------------------------------------------------------


@pytest.mark.django_db
def test_amount_must_be_positive(books):
    with pytest.raises(LedgerError):
        _post(books, SALE, "0")
    assert not LedgerEntry.objects.exists()


@pytest.mark.django_db
def test_an_adjustment_must_explain_itself(books):
    """An unexplained correction is indistinguishable from a mistake later."""
    with pytest.raises(LedgerError):
        post_entry(
            business=books["seller"],
            counterparty=books["colleague"],
            membership=books["seller_m"],
            entry_type=ADJUST_DEBIT,
            amount=Decimal("1"),
            description="",
        )


@pytest.mark.django_db
def test_a_business_cannot_post_against_itself(books):
    with pytest.raises(LedgerError):
        _post(books, counterparty=books["seller"])


@pytest.mark.django_db
def test_ledger_manage_is_required(books):
    """Staff hold ledger.view by default, not ledger.manage."""
    assert not books["staff_m"].has_capability("ledger.manage")
    with pytest.raises(LedgerError):
        _post(books, membership=books["staff_m"])


@pytest.mark.django_db
def test_a_membership_of_another_business_is_refused(books):
    with pytest.raises(LedgerError):
        _post(books, membership=books["other_m"])


# --- manual entries are limited to four ---------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("entry_type", [RECEIVED, PAID, ADJUST_DEBIT, ADJUST_CREDIT])
def test_the_four_manual_entry_types_are_accepted(books, entry_type):
    entry = post_manual_entry(
        business=books["seller"],
        counterparty=books["colleague"],
        membership=books["seller_m"],
        entry_type=entry_type,
        amount=Decimal("500000"),
        description="دلیل",
    )
    assert entry.entry_type == entry_type


@pytest.mark.django_db
@pytest.mark.parametrize("entry_type", [SALE, LedgerEntry.Type.PURCHASE, LedgerEntry.Type.REVERSAL])
def test_a_sale_cannot_be_posted_by_hand(books, entry_type):
    """Sales reach the books through finalizing a Trade — one way in, not two."""
    with pytest.raises(LedgerError):
        post_manual_entry(
            business=books["seller"],
            counterparty=books["colleague"],
            membership=books["seller_m"],
            entry_type=entry_type,
            amount=Decimal("1"),
            description="x",
        )


# --- immutability -------------------------------------------------------------


@pytest.mark.django_db
def test_an_entry_cannot_be_edited(books):
    entry = _post(books)
    entry.amount = Decimal("999")
    with pytest.raises(ValidationError):
        entry.save()


@pytest.mark.django_db
def test_an_entry_cannot_be_deleted(books):
    entry = _post(books)
    with pytest.raises(ValidationError):
        entry.delete()


# --- reversal -----------------------------------------------------------------


@pytest.mark.django_db
def test_a_reversal_negates_the_original_and_reconciles(books):
    entry = _post(books, SALE, "5000000")
    reversal = reverse_entry(entry=entry, membership=books["seller_m"])

    assert reversal.entry_type == LedgerEntry.Type.REVERSAL
    assert reversal.balance_delta == -entry.balance_delta
    assert current_balance(books["seller"], books["colleague"]) == Decimal("0.00")


@pytest.mark.django_db
def test_the_original_is_stamped_but_its_amounts_are_untouched(books):
    entry = _post(books, SALE, "5000000")
    reverse_entry(entry=entry, membership=books["seller_m"])
    entry.refresh_from_db()

    assert entry.reversed_at is not None
    assert entry.amount == Decimal("5000000.00")
    assert entry.balance_delta == Decimal("5000000.00")


@pytest.mark.django_db
def test_an_entry_cannot_be_reversed_twice(books):
    entry = _post(books)
    reverse_entry(entry=entry, membership=books["seller_m"])
    with pytest.raises(LedgerError):
        reverse_entry(entry=entry, membership=books["seller_m"])


@pytest.mark.django_db
def test_a_reversal_cannot_itself_be_reversed(books):
    entry = _post(books)
    reversal = reverse_entry(entry=entry, membership=books["seller_m"])
    with pytest.raises(LedgerError):
        reverse_entry(entry=reversal, membership=books["seller_m"])


# --- tenant isolation ---------------------------------------------------------


@pytest.mark.django_db
def test_a_ledger_belongs_to_one_business_only(books):
    _post(books, SALE, "5000000")

    # The colleague's own books are untouched: a sale to them is not a purchase
    # by them unless they record it themselves.
    assert current_balance(books["colleague"], books["seller"]) == Decimal("0.00")
    assert list(counterparty_balances(books["other"])) == []


@pytest.mark.django_db
def test_the_statement_is_scoped_to_one_counterparty(books):
    _post(books, SALE, "5000000")
    _post(books, SALE, "1000000", counterparty=books["other"])

    entries = counterparty_statement(books["seller"], books["colleague"])
    assert entries.count() == 1
    assert entries.first().amount == Decimal("5000000.00")


# --- statement totals ---------------------------------------------------------


@pytest.mark.django_db
def test_statement_totals_split_the_two_columns(books):
    _post(books, SALE, "5000000")
    _post(books, RECEIVED, "2000000")

    totals = statement_totals(counterparty_statement(books["seller"], books["colleague"]))
    assert totals["debit"] == Decimal("5000000.00")
    assert totals["credit"] == Decimal("2000000.00")
    assert totals["closing"] == Decimal("3000000.00")


@pytest.mark.django_db
def test_statement_totals_of_an_empty_period_invent_no_closing_balance(books):
    _post(books, SALE, "5000000")
    entries = counterparty_statement(
        books["seller"],
        books["colleague"],
        date_from=timezone.localdate() + timedelta(days=1),
    )
    totals = statement_totals(entries)
    assert totals["row_count"] == 0
    assert totals["closing"] is None


# --- business summary ---------------------------------------------------------


@pytest.mark.django_db
def test_the_summary_separates_receivables_from_payables(books):
    _post(books, SALE, "5000000")
    _post(books, RECEIVED, "8000000", counterparty=books["other"])

    summary = business_financial_summary(books["seller"])
    assert summary["receivable_total"] == Decimal("5000000.00")
    # Exposed as a magnitude, never with a minus sign.
    assert summary["payable_total"] == Decimal("8000000.00")
    assert summary["net_balance"] == Decimal("-3000000.00")
    assert summary["net"]["state"] == "creditor"


@pytest.mark.django_db
def test_a_business_with_no_entries_gets_zeros_not_none(books):
    summary = business_financial_summary(books["other"])
    assert summary["receivable_total"] == Decimal("0.00")
    assert summary["contact_count"] == 0


@pytest.mark.django_db
def test_only_colleagues_with_entries_appear_in_the_index(books):
    _post(books, SALE, "1000000")
    names = [b.name for b in counterparty_balances(books["seller"])]
    assert names == ["سنگ همکار"]


@pytest.mark.django_db
def test_there_are_no_legacy_entries_in_a_fresh_database(books):
    _post(books, SALE, "1000000")
    assert not legacy_entries(books["seller"]).exists()
