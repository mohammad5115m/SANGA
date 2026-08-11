from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import Sum

from apps.accounting.models import LedgerEntry
from apps.accounting.selectors import contact_balances, current_balance, describe_balance
from apps.accounting.services import LedgerError, post_entry, reverse_entry
from apps.businesses.models import BusinessMembership
from apps.businesses.services import create_business_for_owner
from apps.contacts.services import create_contact

User = get_user_model()


@pytest.fixture
def setup(db):
    owner_a = User.objects.create_user(phone="09120000201", full_name="مالک الف")
    owner_b = User.objects.create_user(phone="09120000202", full_name="مالک ب")
    staff_user = User.objects.create_user(phone="09120000203", full_name="کارمند")

    biz_a = create_business_for_owner(owner=owner_a, name="سنگ الف", city="محلات")
    biz_b = create_business_for_owner(owner=owner_b, name="سنگ ب", city="تهران")

    m_a = BusinessMembership.objects.get(user=owner_a, business=biz_a)
    m_b = BusinessMembership.objects.get(user=owner_b, business=biz_b)
    staff_m = BusinessMembership.objects.create(
        user=staff_user,
        business=biz_a,
        role=BusinessMembership.Role.STAFF,
        status=BusinessMembership.Status.ACTIVE,
    )

    contact_a = create_contact(
        business=biz_a, membership=m_a, display_name="مشتری الف", is_customer=True
    )
    contact_b = create_contact(
        business=biz_b, membership=m_b, display_name="مشتری ب", is_customer=True
    )
    return {
        "owner_a": owner_a,
        "owner_b": owner_b,
        "staff_user": staff_user,
        "biz_a": biz_a,
        "biz_b": biz_b,
        "m_a": m_a,
        "m_b": m_b,
        "staff_m": staff_m,
        "contact_a": contact_a,
        "contact_b": contact_b,
    }


def _sale(setup, amount):
    return post_entry(
        business=setup["biz_a"],
        contact=setup["contact_a"],
        membership=setup["m_a"],
        entry_type=LedgerEntry.Type.SALE,
        amount=amount,
    )


def test_sale_increases_balance_they_owe_us(setup):
    _sale(setup, Decimal("1000000"))
    balance = current_balance(setup["biz_a"], setup["contact_a"])
    assert balance == Decimal("1000000.00")
    assert describe_balance(balance)["state"] == "they_owe"


def test_payment_received_reduces_balance(setup):
    _sale(setup, Decimal("1000000"))
    post_entry(
        business=setup["biz_a"],
        contact=setup["contact_a"],
        membership=setup["m_a"],
        entry_type=LedgerEntry.Type.PAYMENT_RECEIVED,
        amount=Decimal("400000"),
    )
    assert current_balance(setup["biz_a"], setup["contact_a"]) == Decimal("600000.00")


def test_running_balance_and_reconciliation(setup):
    _sale(setup, Decimal("500000"))
    post_entry(
        business=setup["biz_a"], contact=setup["contact_a"], membership=setup["m_a"],
        entry_type=LedgerEntry.Type.PURCHASE, amount=Decimal("200000"),
    )
    post_entry(
        business=setup["biz_a"], contact=setup["contact_a"], membership=setup["m_a"],
        entry_type=LedgerEntry.Type.PAYMENT_RECEIVED, amount=Decimal("100000"),
    )
    entries = list(
        LedgerEntry.objects.filter(contact=setup["contact_a"]).order_by("created_at")
    )
    # balance_after must equal cumulative sum of deltas at each step.
    running = Decimal("0.00")
    for entry in entries:
        running += entry.balance_delta
        assert entry.balance_after == running
    # cached-free balance reconciles with the sum of all deltas.
    total = LedgerEntry.objects.filter(contact=setup["contact_a"]).aggregate(s=Sum("balance_delta"))["s"]
    assert total == current_balance(setup["biz_a"], setup["contact_a"])
    assert total == Decimal("200000.00")


def test_reversal_negates_original(setup):
    sale = _sale(setup, Decimal("300000"))
    assert current_balance(setup["biz_a"], setup["contact_a"]) == Decimal("300000.00")
    reversal = reverse_entry(entry=sale, membership=setup["m_a"])
    assert reversal.entry_type == LedgerEntry.Type.REVERSAL
    assert reversal.balance_delta == -sale.balance_delta
    assert current_balance(setup["biz_a"], setup["contact_a"]) == Decimal("0.00")


def test_cannot_double_reverse(setup):
    sale = _sale(setup, Decimal("300000"))
    reverse_entry(entry=sale, membership=setup["m_a"])
    with pytest.raises(LedgerError):
        reverse_entry(entry=sale, membership=setup["m_a"])


def test_cannot_reverse_a_reversal(setup):
    sale = _sale(setup, Decimal("300000"))
    reversal = reverse_entry(entry=sale, membership=setup["m_a"])
    with pytest.raises(LedgerError):
        reverse_entry(entry=reversal, membership=setup["m_a"])


def test_adjustment_requires_reason(setup):
    with pytest.raises(LedgerError):
        post_entry(
            business=setup["biz_a"], contact=setup["contact_a"], membership=setup["m_a"],
            entry_type=LedgerEntry.Type.ADJUST_DEBIT, amount=Decimal("50000"),
        )
    entry = post_entry(
        business=setup["biz_a"], contact=setup["contact_a"], membership=setup["m_a"],
        entry_type=LedgerEntry.Type.ADJUST_CREDIT, amount=Decimal("50000"),
        description="تخفیف توافقی",
    )
    assert entry.balance_delta == Decimal("-50000.00")


def test_amount_must_be_positive(setup):
    with pytest.raises(LedgerError):
        _sale(setup, Decimal("0"))
    with pytest.raises(LedgerError):
        _sale(setup, Decimal("-5"))


def test_tenant_isolation_contact_mismatch(setup):
    with pytest.raises(LedgerError):
        post_entry(
            business=setup["biz_b"], contact=setup["contact_a"], membership=setup["m_b"],
            entry_type=LedgerEntry.Type.SALE, amount=Decimal("100"),
        )


def test_membership_business_mismatch(setup):
    with pytest.raises(LedgerError):
        post_entry(
            business=setup["biz_a"], contact=setup["contact_a"], membership=setup["m_b"],
            entry_type=LedgerEntry.Type.SALE, amount=Decimal("100"),
        )


def test_capability_required_to_post(setup):
    with pytest.raises(LedgerError):
        post_entry(
            business=setup["biz_a"], contact=setup["contact_a"], membership=setup["staff_m"],
            entry_type=LedgerEntry.Type.SALE, amount=Decimal("100"),
        )


def test_entry_is_immutable(setup):
    sale = _sale(setup, Decimal("100000"))
    sale.amount = Decimal("999999")
    with pytest.raises(ValidationError):
        sale.save()


def test_entry_cannot_be_deleted(setup):
    sale = _sale(setup, Decimal("100000"))
    with pytest.raises(ValidationError):
        sale.delete()


def test_describe_balance_labels():
    assert describe_balance(Decimal("10"))["state"] == "they_owe"
    assert describe_balance(Decimal("-10"))["state"] == "we_owe"
    assert describe_balance(Decimal("0"))["state"] == "settled"


def test_statement_view_tenant_isolation(client, setup):
    client.force_login(setup["owner_b"])
    resp = client.get(f"/app/accounting/contacts/{setup['contact_a'].id}/")
    assert resp.status_code == 404


def test_add_entry_requires_manage_capability(client, setup):
    client.force_login(setup["staff_user"])
    resp = client.get(f"/app/accounting/contacts/{setup['contact_a'].id}/add/")
    assert resp.status_code == 302


def test_staff_can_view_statement(client, setup):
    client.force_login(setup["staff_user"])
    resp = client.get(f"/app/accounting/contacts/{setup['contact_a'].id}/")
    assert resp.status_code == 200


def test_ledger_index_lists_only_this_businesses_contacts(setup):
    _sale(setup, Decimal("250000"))
    post_entry(
        business=setup["biz_b"],
        contact=setup["contact_b"],
        membership=setup["m_b"],
        entry_type=LedgerEntry.Type.SALE,
        amount=Decimal("999999"),
    )
    rows = list(contact_balances(setup["biz_a"]))
    assert [row.display_name for row in rows] == ["مشتری الف"]
    assert rows[0].balance == Decimal("250000.00")


def test_ledger_index_balances_match_current_balance(setup):
    _sale(setup, Decimal("500000"))
    post_entry(
        business=setup["biz_a"],
        contact=setup["contact_a"],
        membership=setup["m_a"],
        entry_type=LedgerEntry.Type.PAYMENT_RECEIVED,
        amount=Decimal("200000"),
    )
    row = contact_balances(setup["biz_a"]).get(pk=setup["contact_a"].pk)
    assert row.balance == current_balance(setup["biz_a"], setup["contact_a"])


def test_ledger_index_shows_a_contact_without_entries_as_zero(setup):
    row = contact_balances(setup["biz_a"]).get(pk=setup["contact_a"].pk)
    assert row.balance == Decimal("0.00")
    assert row.entry_count == 0


def test_ledger_index_requires_ledger_view(client, setup):
    viewer_user = User.objects.create_user(phone="09120000204", full_name="بازدیدکننده")
    BusinessMembership.objects.create(
        user=viewer_user,
        business=setup["biz_a"],
        role=BusinessMembership.Role.VIEWER,
        status=BusinessMembership.Status.ACTIVE,
    )
    client.force_login(viewer_user)
    assert client.get("/app/accounting/").status_code == 302

    # Staff hold ledger.view by role default and reach the index.
    client.force_login(setup["staff_user"])
    assert client.get("/app/accounting/").status_code == 200


def test_ledger_index_is_linked_from_the_shell_for_ledger_viewers(client, setup):
    client.force_login(setup["staff_user"])
    dashboard = client.get("/app/")
    assert dashboard.status_code == 200
    assert "/app/accounting/" in dashboard.content.decode()


def test_entry_form_and_print_render(client, setup):
    _sale(setup, Decimal("250000"))
    client.force_login(setup["owner_a"])
    assert client.get(f"/app/accounting/contacts/{setup['contact_a'].id}/add/").status_code == 200
    assert client.get(f"/app/accounting/contacts/{setup['contact_a'].id}/print/").status_code == 200
