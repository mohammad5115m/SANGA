from __future__ import annotations

from decimal import Decimal

import pytest

from apps.reservations.services import (
    ReservationError,
    approve_reservation,
    request_reservation,
)

# NOTE: SQLite treats select_for_update() as a no-op, so true row-level
# concurrency cannot be exercised here. These tests verify the availability
# guard prevents over-reservation across overlapping holds; on PostgreSQL the
# same guard runs under an actual row lock.


@pytest.mark.django_db
def test_overlapping_approvals_cannot_oversell(reservation_setup):
    lot = reservation_setup["lot"]  # 100 m² available
    first = request_reservation(
        lot=lot,
        requester_business=reservation_setup["buyer"],
        membership=reservation_setup["buyer_m"],
        quantity_sqm=Decimal("60"),
    )
    second = request_reservation(
        lot=lot,
        requester_business=reservation_setup["other"],
        membership=reservation_setup["other_m"],
        quantity_sqm=Decimal("60"),
    )

    approve_reservation(reservation=first, membership=reservation_setup["seller_m"])
    with pytest.raises(ReservationError):
        approve_reservation(reservation=second, membership=reservation_setup["seller_m"])

    lot.refresh_from_db()
    assert lot.available_sqm == Decimal("40")


@pytest.mark.django_db
def test_second_request_fits_after_first_released(reservation_setup):
    from apps.reservations.services import cancel_reservation

    lot = reservation_setup["lot"]
    first = request_reservation(
        lot=lot,
        requester_business=reservation_setup["buyer"],
        membership=reservation_setup["buyer_m"],
        quantity_sqm=Decimal("100"),
    )
    approve_reservation(reservation=first, membership=reservation_setup["seller_m"])
    cancel_reservation(reservation=first, membership=reservation_setup["seller_m"])

    second = request_reservation(
        lot=lot,
        requester_business=reservation_setup["other"],
        membership=reservation_setup["other_m"],
        quantity_sqm=Decimal("100"),
    )
    approve_reservation(reservation=second, membership=reservation_setup["seller_m"])
    lot.refresh_from_db()
    assert lot.available_sqm == Decimal("0")
