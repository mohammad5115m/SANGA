from __future__ import annotations

from decimal import Decimal

import pytest

from apps.businesses.models import BusinessMembership
from apps.purchase_requests.models import PurchaseOffer, PurchaseRequest
from apps.purchase_requests.services import (
    PurchaseRequestError,
    create_purchase_request,
    decide_offer,
    submit_private_offer,
)
from apps.reservations.models import Reservation


def _open_pr(reservation_setup):
    return create_purchase_request(
        business=reservation_setup["buyer"],
        membership=reservation_setup["buyer_m"],
        title="نیاز تراورتن سفید",
        stone_type="تراورتن",
        color="سفید",
        required_qty_sqm=Decimal("50"),
        similar_accepted=True,
    )


@pytest.mark.django_db
def test_accepting_offer_with_lot_creates_approved_reservation(reservation_setup):
    pr = _open_pr(reservation_setup)
    offer = submit_private_offer(
        purchase_request=pr,
        seller_business=reservation_setup["seller"],
        membership=reservation_setup["seller_m"],
        unit_price=Decimal("950000"),
        offered_qty_sqm=Decimal("50"),
        lot=reservation_setup["lot"],
    )
    decide_offer(offer=offer, membership=reservation_setup["buyer_m"], accept=True)

    reservation = Reservation.objects.get(source_offer=offer)
    assert reservation.status == Reservation.Status.APPROVED
    assert reservation.seller_business_id == reservation_setup["seller"].id
    assert reservation.requester_business_id == reservation_setup["buyer"].id
    assert reservation.quantity_sqm == Decimal("50")

    lot = reservation_setup["lot"]
    lot.refresh_from_db()
    assert lot.available_sqm == Decimal("50")


@pytest.mark.django_db
def test_accepting_offer_without_lot_closes_pr_without_reservation(reservation_setup):
    pr = _open_pr(reservation_setup)
    offer = submit_private_offer(
        purchase_request=pr,
        seller_business=reservation_setup["seller"],
        membership=reservation_setup["seller_m"],
        unit_price=Decimal("950000"),
        offered_qty_sqm=Decimal("50"),
        lot=None,
    )
    decide_offer(offer=offer, membership=reservation_setup["buyer_m"], accept=True)

    pr.refresh_from_db()
    assert pr.status == PurchaseRequest.Status.CLOSED
    assert not Reservation.objects.filter(source_offer=offer).exists()


@pytest.mark.django_db
def test_accept_rolls_back_when_lot_insufficient(reservation_setup):
    pr = _open_pr(reservation_setup)
    offer = submit_private_offer(
        purchase_request=pr,
        seller_business=reservation_setup["seller"],
        membership=reservation_setup["seller_m"],
        unit_price=Decimal("950000"),
        offered_qty_sqm=Decimal("50"),
        lot=reservation_setup["lot"],
    )
    lot = reservation_setup["lot"]
    lot.available_sqm = Decimal("10")
    lot.save(update_fields=["available_sqm"])

    from apps.reservations.services import ReservationError

    with pytest.raises(ReservationError):
        decide_offer(offer=offer, membership=reservation_setup["buyer_m"], accept=True)

    # Everything rolled back: offer still submitted, PR still open, no reservation.
    offer.refresh_from_db()
    pr.refresh_from_db()
    assert offer.status == PurchaseOffer.Status.SUBMITTED
    assert pr.status != PurchaseRequest.Status.CLOSED
    assert not Reservation.objects.filter(source_offer=offer).exists()
    lot.refresh_from_db()
    assert lot.available_sqm == Decimal("10")


@pytest.mark.django_db
def test_accept_requires_reservations_manage_capability(reservation_setup):
    pr = _open_pr(reservation_setup)
    offer = submit_private_offer(
        purchase_request=pr,
        seller_business=reservation_setup["seller"],
        membership=reservation_setup["seller_m"],
        unit_price=Decimal("950000"),
        offered_qty_sqm=Decimal("50"),
        lot=reservation_setup["lot"],
    )
    # A viewer on the buyer lacks reservations.manage.
    from django.contrib.auth import get_user_model

    User = get_user_model()
    weak_user = User.objects.create_user(phone="09120000009", full_name="ناظر خریدار")
    weak_m = BusinessMembership.objects.create(
        user=weak_user,
        business=reservation_setup["buyer"],
        role=BusinessMembership.Role.VIEWER,
        status=BusinessMembership.Status.ACTIVE,
    )
    with pytest.raises(PurchaseRequestError):
        decide_offer(offer=offer, membership=weak_m, accept=True)

    offer.refresh_from_db()
    assert offer.status == PurchaseOffer.Status.SUBMITTED
