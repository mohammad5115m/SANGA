from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.accounting.models import LedgerEntry
from apps.accounting.selectors import (
    current_balance,
    suggested_contact_for_reservation,
    suggested_trade_amount,
    trade_entry_for_reservation,
)
from apps.accounting.services import (
    LedgerDuplicateError,
    LedgerError,
    post_trade_entry,
    reverse_entry,
)
from apps.businesses.models import BusinessMembership
from apps.businesses.services import add_warehouse, create_business_for_owner
from apps.contacts.services import ContactError, create_contact
from apps.inventory.models import InventoryLot, Product
from apps.partners.models import PartnerRelation
from apps.pricing.services import ensure_default_tiers, set_lot_prices
from apps.reservations.models import Reservation
from apps.reservations.services import (
    approve_reservation,
    convert_reservation,
    request_reservation,
)

User = get_user_model()


@pytest.fixture
def trade(db):
    ensure_default_tiers()
    seller_user = User.objects.create_user(phone="09120000301", full_name="فروشنده")
    buyer_user = User.objects.create_user(phone="09120000302", full_name="خریدار")
    staff_user = User.objects.create_user(phone="09120000303", full_name="کارمند فروشنده")

    seller = create_business_for_owner(owner=seller_user, name="سنگ فروشنده", city="محلات")
    buyer = create_business_for_owner(owner=buyer_user, name="سنگ خریدار", city="تهران")

    seller_m = BusinessMembership.objects.get(user=seller_user, business=seller)
    buyer_m = BusinessMembership.objects.get(user=buyer_user, business=buyer)
    # Staff has ledger.view but not ledger.manage by role default.
    staff_m = BusinessMembership.objects.create(
        user=staff_user,
        business=seller,
        role=BusinessMembership.Role.STAFF,
        status=BusinessMembership.Status.ACTIVE,
    )

    warehouse = add_warehouse(business=seller, name="انبار محلات", city="محلات", is_default=True)
    product = Product.objects.create(
        business=seller, commercial_name="تراورتن عباس‌آباد", stone_type="تراورتن"
    )
    lot = InventoryLot.objects.create(
        business=seller,
        product=product,
        warehouse=warehouse,
        lot_code="TRD-1",
        status=InventoryLot.Status.AVAILABLE,
        visibility=InventoryLot.Visibility.ALL_PARTNERS,
        available_sqm=Decimal("100"),
        original_sqm=Decimal("100"),
        inventory_confirmed_at=timezone.now(),
    )
    set_lot_prices(lot=lot, b2b_amount=Decimal("1000000"), b2c_amount=Decimal("1500000"))

    seller_contact = create_contact(
        business=seller, membership=seller_m, display_name="سنگ خریدار", is_customer=True
    )
    buyer_contact = create_contact(
        business=buyer, membership=buyer_m, display_name="مخاطب خریدار", is_customer=True
    )
    return {
        "seller": seller,
        "buyer": buyer,
        "seller_user": seller_user,
        "buyer_user": buyer_user,
        "staff_user": staff_user,
        "seller_m": seller_m,
        "buyer_m": buyer_m,
        "staff_m": staff_m,
        "lot": lot,
        "seller_contact": seller_contact,
        "buyer_contact": buyer_contact,
    }


def _converted(trade, quantity=Decimal("40")) -> Reservation:
    reservation = request_reservation(
        lot=trade["lot"],
        requester_business=trade["buyer"],
        membership=trade["buyer_m"],
        quantity_sqm=quantity,
    )
    approve_reservation(reservation=reservation, membership=trade["seller_m"])
    convert_reservation(reservation=reservation, membership=trade["seller_m"])
    reservation.refresh_from_db()
    return reservation


def _post(trade, reservation, amount=Decimal("40000000")):
    return post_trade_entry(
        reservation=reservation,
        business=trade["seller"],
        contact=trade["seller_contact"],
        membership=trade["seller_m"],
        amount=amount,
    )


def test_trade_entry_posts_once_with_correct_signed_balance(trade):
    reservation = _converted(trade)
    entry = _post(trade, reservation)

    assert LedgerEntry.objects.filter(business=trade["seller"]).count() == 1
    assert entry.entry_type == LedgerEntry.Type.SALE
    assert entry.amount == Decimal("40000000.00")
    # A sale increases what the contact owes us.
    assert entry.balance_delta == Decimal("40000000.00")
    assert entry.balance_after == Decimal("40000000.00")
    assert entry.related_reservation_id == reservation.id
    assert entry.related_lot_id == trade["lot"].id
    assert entry.description
    assert current_balance(trade["seller"], trade["seller_contact"]) == Decimal("40000000.00")


def test_second_post_is_refused_and_leaves_exactly_one_entry(trade):
    reservation = _converted(trade)
    _post(trade, reservation)

    with pytest.raises(LedgerDuplicateError) as exc_info:
        _post(trade, reservation)

    assert exc_info.value.message == "سند مالی این معامله قبلاً ثبت شده است."
    assert exc_info.value.existing is not None
    assert LedgerEntry.objects.filter(related_reservation=reservation).count() == 1


def test_retry_with_a_different_contact_still_creates_only_one_entry(trade):
    reservation = _converted(trade)
    _post(trade, reservation)
    other_contact = create_contact(
        business=trade["seller"],
        membership=trade["seller_m"],
        display_name="مخاطب دوم",
        is_customer=True,
    )

    with pytest.raises(LedgerDuplicateError):
        post_trade_entry(
            reservation=reservation,
            business=trade["seller"],
            contact=other_contact,
            membership=trade["seller_m"],
            amount=Decimal("40000000"),
        )
    assert LedgerEntry.objects.filter(related_reservation=reservation).count() == 1


def test_db_constraint_rejects_a_second_trade_entry(trade):
    reservation = _converted(trade)
    _post(trade, reservation)

    # Bypass the service entirely: the database itself must refuse.
    with pytest.raises(IntegrityError), transaction.atomic():
        LedgerEntry.objects.create(
            business=trade["seller"],
            contact=trade["seller_contact"],
            entry_type=LedgerEntry.Type.SALE,
            amount=Decimal("1000.00"),
            balance_delta=Decimal("1000.00"),
            balance_after=Decimal("40001000.00"),
            occurred_on=timezone.localdate(),
            related_reservation=reservation,
        )


def test_reversal_of_a_trade_entry_is_allowed_and_balance_reconciles(trade):
    reservation = _converted(trade)
    entry = _post(trade, reservation)

    reversal = reverse_entry(entry=entry, membership=trade["seller_m"])
    assert reversal.entry_type == LedgerEntry.Type.REVERSAL
    assert reversal.balance_delta == -entry.balance_delta

    entries = LedgerEntry.objects.filter(business=trade["seller"], contact=trade["seller_contact"])
    total = entries.aggregate(total=Sum("balance_delta"))["total"]
    assert total == current_balance(trade["seller"], trade["seller_contact"]) == Decimal("0.00")
    assert entries.count() == 2


def test_reversed_trade_can_be_recorded_again_with_the_reservation_link(trade):
    reservation = _converted(trade)
    wrong = _post(trade, reservation, amount=Decimal("99999999"))
    reverse_entry(entry=wrong, membership=trade["seller_m"])

    wrong.refresh_from_db()
    assert wrong.reversed_at is not None
    # The slot is free again, so the pre-check no longer reports a duplicate.
    assert trade_entry_for_reservation(trade["seller"], reservation) is None

    corrected = _post(trade, reservation, amount=Decimal("40000000"))
    assert corrected.related_reservation_id == reservation.id
    assert corrected.related_lot_id == trade["lot"].id
    assert trade_entry_for_reservation(trade["seller"], reservation) == corrected


def test_balance_reconciles_after_reverse_and_repost(trade):
    reservation = _converted(trade)
    wrong = _post(trade, reservation, amount=Decimal("99999999"))
    reverse_entry(entry=wrong, membership=trade["seller_m"])
    _post(trade, reservation, amount=Decimal("40000000"))

    entries = LedgerEntry.objects.filter(business=trade["seller"], contact=trade["seller_contact"])
    total = entries.aggregate(total=Sum("balance_delta"))["total"]
    assert entries.count() == 3
    assert total == current_balance(trade["seller"], trade["seller_contact"]) == Decimal("40000000.00")


def test_reposting_after_a_reversal_is_still_blocked_the_second_time(trade):
    reservation = _converted(trade)
    wrong = _post(trade, reservation, amount=Decimal("99999999"))
    reverse_entry(entry=wrong, membership=trade["seller_m"])
    _post(trade, reservation, amount=Decimal("40000000"))

    with pytest.raises(LedgerDuplicateError):
        _post(trade, reservation, amount=Decimal("40000000"))
    assert (
        LedgerEntry.objects.filter(
            related_reservation=reservation,
            entry_type=LedgerEntry.Type.SALE,
        ).count()
        == 2
    )


def test_db_constraint_still_rejects_a_second_live_trade_entry_after_a_reversal(trade):
    reservation = _converted(trade)
    wrong = _post(trade, reservation, amount=Decimal("99999999"))
    reverse_entry(entry=wrong, membership=trade["seller_m"])
    _post(trade, reservation, amount=Decimal("40000000"))

    # Bypass the service: the partial unique index must still refuse a second
    # un-reversed trade entry for the same reservation.
    with pytest.raises(IntegrityError), transaction.atomic():
        LedgerEntry.objects.create(
            business=trade["seller"],
            contact=trade["seller_contact"],
            entry_type=LedgerEntry.Type.SALE,
            amount=Decimal("1000.00"),
            balance_delta=Decimal("1000.00"),
            balance_after=Decimal("40001000.00"),
            occurred_on=timezone.localdate(),
            related_reservation=reservation,
        )


def test_an_entry_cannot_be_reversed_twice(trade):
    reservation = _converted(trade)
    entry = _post(trade, reservation)
    reverse_entry(entry=entry, membership=trade["seller_m"])

    with pytest.raises(LedgerError):
        reverse_entry(entry=entry, membership=trade["seller_m"])
    assert LedgerEntry.objects.filter(reverses=entry).count() == 1


def test_a_reversal_cannot_itself_be_reversed(trade):
    reservation = _converted(trade)
    entry = _post(trade, reservation)
    reversal = reverse_entry(entry=entry, membership=trade["seller_m"])

    with pytest.raises(LedgerError):
        reverse_entry(entry=reversal, membership=trade["seller_m"])
    reversal.refresh_from_db()
    assert reversal.reversed_at is None


def test_unrelated_reservations_each_get_their_own_trade_entry(trade):
    first = _converted(trade, quantity=Decimal("10"))
    second = _converted(trade, quantity=Decimal("20"))
    _post(trade, first, amount=Decimal("10000000"))
    _post(trade, second, amount=Decimal("20000000"))

    assert LedgerEntry.objects.filter(business=trade["seller"]).count() == 2
    assert current_balance(trade["seller"], trade["seller_contact"]) == Decimal("30000000.00")


def test_ledger_manage_is_required_at_the_service_layer(trade):
    reservation = _converted(trade)
    with pytest.raises(LedgerError):
        post_trade_entry(
            reservation=reservation,
            business=trade["seller"],
            contact=trade["seller_contact"],
            membership=trade["staff_m"],
            amount=Decimal("40000000"),
        )
    assert not LedgerEntry.objects.exists()


def test_cannot_post_against_another_business_contact(trade):
    reservation = _converted(trade)
    with pytest.raises(LedgerError):
        post_trade_entry(
            reservation=reservation,
            business=trade["seller"],
            contact=trade["buyer_contact"],
            membership=trade["seller_m"],
            amount=Decimal("40000000"),
        )
    assert not LedgerEntry.objects.exists()


def test_buyer_cannot_post_against_the_sellers_reservation(trade):
    reservation = _converted(trade)
    with pytest.raises(LedgerError):
        post_trade_entry(
            reservation=reservation,
            business=trade["buyer"],
            contact=trade["buyer_contact"],
            membership=trade["buyer_m"],
            amount=Decimal("40000000"),
        )
    assert not LedgerEntry.objects.exists()


def test_membership_of_another_business_is_refused(trade):
    reservation = _converted(trade)
    with pytest.raises(LedgerError):
        post_trade_entry(
            reservation=reservation,
            business=trade["seller"],
            contact=trade["seller_contact"],
            membership=trade["buyer_m"],
            amount=Decimal("40000000"),
        )
    assert not LedgerEntry.objects.exists()


def test_cannot_post_before_the_reservation_is_converted(trade):
    reservation = request_reservation(
        lot=trade["lot"],
        requester_business=trade["buyer"],
        membership=trade["buyer_m"],
        quantity_sqm=Decimal("15"),
    )
    approve_reservation(reservation=reservation, membership=trade["seller_m"])
    reservation.refresh_from_db()
    with pytest.raises(LedgerError):
        _post(trade, reservation, amount=Decimal("15000000"))
    assert not LedgerEntry.objects.exists()


def test_amount_must_be_positive(trade):
    reservation = _converted(trade)
    with pytest.raises(LedgerError):
        _post(trade, reservation, amount=Decimal("0"))
    assert not LedgerEntry.objects.exists()


def test_suggested_amount_uses_b2b_price_times_quantity(trade):
    reservation = _converted(trade, quantity=Decimal("12.5"))
    assert suggested_trade_amount(reservation) == Decimal("12500000.00")


def test_suggested_amount_is_blank_without_a_price(trade):
    reservation = _converted(trade)
    trade["lot"].prices.all().delete()
    assert suggested_trade_amount(reservation) is None


def test_suggested_contact_only_when_link_is_unambiguous(trade):
    reservation = _converted(trade)
    assert suggested_contact_for_reservation(trade["seller"], reservation) is None

    PartnerRelation.objects.create(
        supplier_business=trade["seller"],
        partner_business=trade["buyer"],
        status=PartnerRelation.Status.APPROVED,
    )
    linked = create_contact(
        business=trade["seller"],
        membership=trade["seller_m"],
        display_name="خریدار (متصل)",
        is_customer=True,
        linked_business=trade["buyer"],
    )
    assert suggested_contact_for_reservation(trade["seller"], reservation) == linked

    # A second contact linked to the same buyer is refused outright, so the
    # suggestion can never become ambiguous through this route.
    with pytest.raises(ContactError):
        create_contact(
            business=trade["seller"],
            membership=trade["seller_m"],
            display_name="خریدار (متصل دوم)",
            is_customer=True,
            linked_business=trade["buyer"],
        )
    assert suggested_contact_for_reservation(trade["seller"], reservation) == linked


def test_trade_entry_lookup_is_tenant_scoped(trade):
    reservation = _converted(trade)
    _post(trade, reservation)
    assert trade_entry_for_reservation(trade["seller"], reservation) is not None
    assert trade_entry_for_reservation(trade["buyer"], reservation) is None


def _record_url(reservation) -> str:
    return f"/app/accounting/reservations/{reservation.id}/record/"


def _record_payload(trade, amount="40000000"):
    return {
        "action": "record",
        "contact": str(trade["seller_contact"].id),
        "amount": amount,
        "occurred_on": timezone.localdate().isoformat(),
        "description": "",
        "reference": "",
        "confirm": "on",
    }


def test_record_trade_screen_renders_for_the_seller(client, trade):
    reservation = _converted(trade)
    client.force_login(trade["seller_user"])
    resp = client.get(_record_url(reservation))
    assert resp.status_code == 200
    assert "ثبت سند مالی این معامله" in resp.content.decode()


def test_record_trade_view_posts_a_single_entry(client, trade):
    reservation = _converted(trade)
    client.force_login(trade["seller_user"])
    resp = client.post(_record_url(reservation), _record_payload(trade))
    assert resp.status_code == 302
    assert LedgerEntry.objects.filter(related_reservation=reservation).count() == 1


def test_record_trade_view_requires_confirmation(client, trade):
    reservation = _converted(trade)
    client.force_login(trade["seller_user"])
    payload = _record_payload(trade)
    payload.pop("confirm")
    resp = client.post(_record_url(reservation), payload)
    assert resp.status_code == 200
    assert not LedgerEntry.objects.exists()


def test_record_trade_view_tolerates_a_double_post(client, trade):
    reservation = _converted(trade)
    client.force_login(trade["seller_user"])
    client.post(_record_url(reservation), _record_payload(trade))
    resp = client.post(_record_url(reservation), _record_payload(trade), follow=True)

    assert resp.status_code == 200
    assert "سند مالی این معامله قبلاً ثبت شده است." in resp.content.decode()
    assert LedgerEntry.objects.filter(related_reservation=reservation).count() == 1


def test_record_trade_view_requires_ledger_manage(client, trade):
    reservation = _converted(trade)
    client.force_login(trade["staff_user"])
    resp = client.get(_record_url(reservation))
    assert resp.status_code == 302
    assert not LedgerEntry.objects.exists()


def test_record_trade_view_is_not_reachable_by_the_buyer(client, trade):
    reservation = _converted(trade)
    client.force_login(trade["buyer_user"])
    assert client.get(_record_url(reservation)).status_code == 404
    assert client.post(_record_url(reservation), _record_payload(trade)).status_code == 404
    assert not LedgerEntry.objects.exists()


def test_record_trade_view_can_create_a_contact_inline(client, trade):
    reservation = _converted(trade)
    client.force_login(trade["seller_user"])
    resp = client.post(
        _record_url(reservation),
        {"action": "create_contact", "display_name": "مشتری تازه", "phone": "09121110000"},
    )
    assert resp.status_code == 302
    assert "contact=" in resp["Location"]
    assert not LedgerEntry.objects.exists()
