from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.inventory.models import InventoryLot
from apps.reservations.models import Reservation
from apps.reservations.services import (
    approve_reservation,
    expire_due_reservations,
    request_reservation,
)


def _approved(reservation_setup, qty="100"):
    reservation = request_reservation(
        lot=reservation_setup["lot"],
        requester_business=reservation_setup["buyer"],
        membership=reservation_setup["buyer_m"],
        quantity_sqm=Decimal(qty),
    )
    approve_reservation(reservation=reservation, membership=reservation_setup["seller_m"])
    return reservation


@pytest.mark.django_db
def test_expiration_releases_quantity(reservation_setup):
    reservation = _approved(reservation_setup)
    reservation.expires_at = timezone.now() - timedelta(hours=1)
    reservation.save(update_fields=["expires_at"])

    result = expire_due_reservations()
    assert result["expired"] == 1

    reservation.refresh_from_db()
    lot = reservation_setup["lot"]
    lot.refresh_from_db()
    assert reservation.status == Reservation.Status.EXPIRED
    assert lot.available_sqm == Decimal("100")
    assert lot.status == InventoryLot.Status.AVAILABLE
    assert reservation.released_at is not None


@pytest.mark.django_db
def test_expiration_is_idempotent(reservation_setup):
    reservation = _approved(reservation_setup, qty="60")
    reservation.expires_at = timezone.now() - timedelta(hours=1)
    reservation.save(update_fields=["expires_at"])

    first = expire_due_reservations()
    second = expire_due_reservations()
    assert first["expired"] == 1
    assert second["expired"] == 0

    lot = reservation_setup["lot"]
    lot.refresh_from_db()
    # Quantity released exactly once (no double refund).
    assert lot.available_sqm == Decimal("100")


@pytest.mark.django_db
def test_not_yet_due_reservation_is_untouched(reservation_setup):
    reservation = _approved(reservation_setup, qty="30")
    reservation.expires_at = timezone.now() + timedelta(hours=5)
    reservation.save(update_fields=["expires_at"])

    result = expire_due_reservations()
    assert result["expired"] == 0
    reservation.refresh_from_db()
    assert reservation.status == Reservation.Status.APPROVED
