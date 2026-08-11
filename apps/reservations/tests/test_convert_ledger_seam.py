from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from apps.accounting.models import LedgerEntry
from apps.accounting.selectors import current_balance
from apps.accounting.services import LedgerError, TradeEntryRequest
from apps.businesses.models import BusinessMembership
from apps.contacts.services import create_contact
from apps.reservations.models import Reservation
from apps.reservations.services import (
    approve_reservation,
    convert_reservation,
    request_reservation,
)

User = get_user_model()


def _approved(reservation_setup, quantity=Decimal("30")) -> Reservation:
    reservation = request_reservation(
        lot=reservation_setup["lot"],
        requester_business=reservation_setup["buyer"],
        membership=reservation_setup["buyer_m"],
        quantity_sqm=quantity,
    )
    approve_reservation(reservation=reservation, membership=reservation_setup["seller_m"])
    reservation.refresh_from_db()
    return reservation


def _seller_contact(reservation_setup):
    return create_contact(
        business=reservation_setup["seller"],
        membership=reservation_setup["seller_m"],
        display_name="خریدار سنگ",
        is_customer=True,
    )


@pytest.mark.django_db
def test_convert_without_opting_in_creates_no_ledger_entry(reservation_setup):
    reservation = _approved(reservation_setup)
    convert_reservation(reservation=reservation, membership=reservation_setup["seller_m"])

    reservation.refresh_from_db()
    assert reservation.status == Reservation.Status.CONVERTED
    assert not LedgerEntry.objects.exists()


@pytest.mark.django_db
def test_convert_with_opt_in_records_exactly_one_entry(reservation_setup):
    reservation = _approved(reservation_setup)
    contact = _seller_contact(reservation_setup)

    convert_reservation(
        reservation=reservation,
        membership=reservation_setup["seller_m"],
        trade_entry=TradeEntryRequest(contact=contact, amount=Decimal("30000000")),
    )

    entries = LedgerEntry.objects.filter(related_reservation=reservation)
    assert entries.count() == 1
    entry = entries.get()
    assert entry.entry_type == LedgerEntry.Type.SALE
    assert entry.balance_delta == Decimal("30000000.00")
    assert current_balance(reservation_setup["seller"], contact) == Decimal("30000000.00")


@pytest.mark.django_db
def test_opt_in_failure_rolls_back_the_whole_conversion(reservation_setup):
    reservation = _approved(reservation_setup)
    # A contact of another business must be refused, and the conversion must not stick.
    foreign_contact = create_contact(
        business=reservation_setup["buyer"],
        membership=reservation_setup["buyer_m"],
        display_name="مخاطب خریدار",
        is_customer=True,
    )

    with pytest.raises(LedgerError):
        convert_reservation(
            reservation=reservation,
            membership=reservation_setup["seller_m"],
            trade_entry=TradeEntryRequest(contact=foreign_contact, amount=Decimal("30000000")),
        )

    reservation.refresh_from_db()
    assert reservation.status == Reservation.Status.APPROVED
    assert not LedgerEntry.objects.exists()


@pytest.mark.django_db
def test_opt_in_requires_ledger_manage_on_top_of_reservations_manage(reservation_setup):
    reservation = _approved(reservation_setup)
    contact = _seller_contact(reservation_setup)
    # Staff may convert reservations but may not post ledger entries.
    staff_user = User.objects.create_user(phone="09120000401", full_name="کارمند فروشنده")
    staff_m = BusinessMembership.objects.create(
        user=staff_user,
        business=reservation_setup["seller"],
        role=BusinessMembership.Role.STAFF,
        status=BusinessMembership.Status.ACTIVE,
    )

    with pytest.raises(LedgerError):
        convert_reservation(
            reservation=reservation,
            membership=staff_m,
            trade_entry=TradeEntryRequest(contact=contact, amount=Decimal("30000000")),
        )

    reservation.refresh_from_db()
    assert reservation.status == Reservation.Status.APPROVED
    assert not LedgerEntry.objects.exists()
