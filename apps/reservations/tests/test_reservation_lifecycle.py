from __future__ import annotations

from decimal import Decimal

import pytest

from apps.inventory.models import InventoryLot
from apps.reservations.models import Reservation
from apps.reservations.services import (
    ReservationError,
    approve_reservation,
    cancel_reservation,
    convert_reservation,
    extend_reservation,
    reject_reservation,
    request_reservation,
)


@pytest.mark.django_db
def test_request_then_approve_locks_quantity(reservation_setup):
    lot = reservation_setup["lot"]
    reservation = request_reservation(
        lot=lot,
        requester_business=reservation_setup["buyer"],
        membership=reservation_setup["buyer_m"],
        quantity_sqm=Decimal("60"),
    )
    assert reservation.status == Reservation.Status.REQUESTED
    # Requesting does not deduct.
    lot.refresh_from_db()
    assert lot.available_sqm == Decimal("100")

    approve_reservation(reservation=reservation, membership=reservation_setup["seller_m"])
    reservation.refresh_from_db()
    lot.refresh_from_db()
    assert reservation.status == Reservation.Status.APPROVED
    assert reservation.expires_at is not None
    assert lot.available_sqm == Decimal("40")


@pytest.mark.django_db
def test_approve_full_quantity_marks_lot_reserved(reservation_setup):
    lot = reservation_setup["lot"]
    reservation = request_reservation(
        lot=lot,
        requester_business=reservation_setup["buyer"],
        membership=reservation_setup["buyer_m"],
        quantity_sqm=Decimal("100"),
    )
    approve_reservation(reservation=reservation, membership=reservation_setup["seller_m"])
    lot.refresh_from_db()
    assert lot.available_sqm == Decimal("0")
    assert lot.status == InventoryLot.Status.RESERVED


@pytest.mark.django_db
def test_cannot_request_own_lot(reservation_setup):
    with pytest.raises(ReservationError):
        request_reservation(
            lot=reservation_setup["lot"],
            requester_business=reservation_setup["seller"],
            membership=reservation_setup["seller_m"],
            quantity_sqm=Decimal("10"),
        )


@pytest.mark.django_db
def test_request_more_than_available_rejected(reservation_setup):
    with pytest.raises(ReservationError):
        request_reservation(
            lot=reservation_setup["lot"],
            requester_business=reservation_setup["buyer"],
            membership=reservation_setup["buyer_m"],
            quantity_sqm=Decimal("500"),
        )


@pytest.mark.django_db
def test_approve_insufficient_quantity_raises_and_no_deduction(reservation_setup):
    lot = reservation_setup["lot"]
    reservation = request_reservation(
        lot=lot,
        requester_business=reservation_setup["buyer"],
        membership=reservation_setup["buyer_m"],
        quantity_sqm=Decimal("80"),
    )
    # Drain the lot after the request but before approval.
    lot.available_sqm = Decimal("50")
    lot.save(update_fields=["available_sqm"])

    with pytest.raises(ReservationError):
        approve_reservation(reservation=reservation, membership=reservation_setup["seller_m"])

    reservation.refresh_from_db()
    lot.refresh_from_db()
    assert reservation.status == Reservation.Status.REQUESTED
    assert lot.available_sqm == Decimal("50")


@pytest.mark.django_db
def test_reject_releases_nothing_and_is_terminal(reservation_setup):
    reservation = request_reservation(
        lot=reservation_setup["lot"],
        requester_business=reservation_setup["buyer"],
        membership=reservation_setup["buyer_m"],
        quantity_sqm=Decimal("30"),
    )
    reject_reservation(reservation=reservation, membership=reservation_setup["seller_m"])
    reservation.refresh_from_db()
    assert reservation.status == Reservation.Status.REJECTED
    reservation_setup["lot"].refresh_from_db()
    assert reservation_setup["lot"].available_sqm == Decimal("100")


@pytest.mark.django_db
def test_cancel_approved_releases_quantity(reservation_setup):
    lot = reservation_setup["lot"]
    reservation = request_reservation(
        lot=lot,
        requester_business=reservation_setup["buyer"],
        membership=reservation_setup["buyer_m"],
        quantity_sqm=Decimal("100"),
    )
    approve_reservation(reservation=reservation, membership=reservation_setup["seller_m"])
    lot.refresh_from_db()
    assert lot.status == InventoryLot.Status.RESERVED

    cancel_reservation(reservation=reservation, membership=reservation_setup["buyer_m"])
    reservation.refresh_from_db()
    lot.refresh_from_db()
    assert reservation.status == Reservation.Status.CANCELLED
    assert lot.available_sqm == Decimal("100")
    assert lot.status == InventoryLot.Status.AVAILABLE
    assert reservation.released_at is not None


@pytest.mark.django_db
def test_extend_pushes_expiry(reservation_setup):
    reservation = request_reservation(
        lot=reservation_setup["lot"],
        requester_business=reservation_setup["buyer"],
        membership=reservation_setup["buyer_m"],
        quantity_sqm=Decimal("10"),
    )
    approve_reservation(reservation=reservation, membership=reservation_setup["seller_m"])
    reservation.refresh_from_db()
    before = reservation.expires_at

    extend_reservation(reservation=reservation, membership=reservation_setup["seller_m"], hours=24)
    reservation.refresh_from_db()
    assert reservation.expires_at > before
    assert reservation.extended_count == 1


@pytest.mark.django_db
def test_convert_marks_sale(reservation_setup):
    lot = reservation_setup["lot"]
    reservation = request_reservation(
        lot=lot,
        requester_business=reservation_setup["buyer"],
        membership=reservation_setup["buyer_m"],
        quantity_sqm=Decimal("100"),
    )
    approve_reservation(reservation=reservation, membership=reservation_setup["seller_m"])
    convert_reservation(reservation=reservation, membership=reservation_setup["seller_m"])
    reservation.refresh_from_db()
    lot.refresh_from_db()
    assert reservation.status == Reservation.Status.CONVERTED
    assert lot.status == InventoryLot.Status.SOLD


@pytest.mark.django_db
def test_invalid_transition_double_approve_blocked(reservation_setup):
    reservation = request_reservation(
        lot=reservation_setup["lot"],
        requester_business=reservation_setup["buyer"],
        membership=reservation_setup["buyer_m"],
        quantity_sqm=Decimal("20"),
    )
    approve_reservation(reservation=reservation, membership=reservation_setup["seller_m"])
    with pytest.raises(ReservationError):
        approve_reservation(reservation=reservation, membership=reservation_setup["seller_m"])
    # Quantity deducted exactly once.
    reservation_setup["lot"].refresh_from_db()
    assert reservation_setup["lot"].available_sqm == Decimal("80")


@pytest.mark.django_db
def test_convert_requires_approved(reservation_setup):
    reservation = request_reservation(
        lot=reservation_setup["lot"],
        requester_business=reservation_setup["buyer"],
        membership=reservation_setup["buyer_m"],
        quantity_sqm=Decimal("20"),
    )
    with pytest.raises(ReservationError):
        convert_reservation(reservation=reservation, membership=reservation_setup["seller_m"])
