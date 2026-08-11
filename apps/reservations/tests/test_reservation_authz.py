from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.reservations.selectors import get_reservation_for_business
from apps.reservations.services import (
    ReservationError,
    approve_reservation,
    request_reservation,
)


def _make_reservation(reservation_setup, qty="40"):
    return request_reservation(
        lot=reservation_setup["lot"],
        requester_business=reservation_setup["buyer"],
        membership=reservation_setup["buyer_m"],
        quantity_sqm=Decimal(qty),
    )


@pytest.mark.django_db
def test_unrelated_business_cannot_load_reservation(reservation_setup):
    reservation = _make_reservation(reservation_setup)
    assert get_reservation_for_business(reservation_setup["other"], reservation.id) is None
    # Parties can load it.
    assert get_reservation_for_business(reservation_setup["seller"], reservation.id) is not None
    assert get_reservation_for_business(reservation_setup["buyer"], reservation.id) is not None


@pytest.mark.django_db
def test_requester_cannot_approve(reservation_setup):
    reservation = _make_reservation(reservation_setup)
    with pytest.raises(ReservationError):
        approve_reservation(reservation=reservation, membership=reservation_setup["buyer_m"])


@pytest.mark.django_db
def test_viewer_without_manage_cannot_approve(reservation_setup):
    reservation = _make_reservation(reservation_setup)
    # Viewer belongs to the seller but lacks reservations.manage.
    assert not reservation_setup["seller_viewer_m"].has_capability("reservations.manage")
    with pytest.raises(ReservationError):
        approve_reservation(reservation=reservation, membership=reservation_setup["seller_viewer_m"])


@pytest.mark.django_db
def test_detail_view_hidden_from_unrelated_business(client, reservation_setup):
    reservation = _make_reservation(reservation_setup)
    client.force_login(reservation_setup["other_user"])
    session = client.session
    session["current_business_id"] = str(reservation_setup["other"].id)
    session.save()
    response = client.get(reverse("reservations:detail", kwargs={"reservation_id": reservation.id}))
    # Redirects back to inbox with "not found" rather than exposing it.
    assert response.status_code == 302
    assert reverse("reservations:inbox") in response.url


@pytest.mark.django_db
def test_approve_endpoint_denied_without_manage(client, reservation_setup):
    reservation = _make_reservation(reservation_setup)
    client.force_login(reservation_setup["viewer_user"])
    session = client.session
    session["current_business_id"] = str(reservation_setup["seller"].id)
    session.save()
    response = client.post(reverse("reservations:approve", kwargs={"reservation_id": reservation.id}))
    assert response.status_code == 302
    # Capability decorator bounces to dashboard; no deduction happened.
    reservation.refresh_from_db()
    assert reservation.status == "requested"
    reservation_setup["lot"].refresh_from_db()
    assert reservation_setup["lot"].available_sqm == Decimal("100")
