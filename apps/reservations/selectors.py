from __future__ import annotations

from django.db.models import Q, QuerySet

from apps.businesses.models import Business

from .models import Reservation


def _base_qs() -> QuerySet[Reservation]:
    return Reservation.objects.select_related(
        "lot",
        "lot__product",
        "seller_business",
        "requester_business",
        "source_offer",
    )


def incoming_reservations(business: Business) -> QuerySet[Reservation]:
    """Reservations where this business is the seller (owner of the lot)."""
    return _base_qs().filter(seller_business=business).order_by("-created_at")


def outgoing_reservations(business: Business) -> QuerySet[Reservation]:
    """Reservations this business requested against other businesses' lots."""
    return _base_qs().filter(requester_business=business).order_by("-created_at")


def get_reservation_for_business(business: Business, reservation_id) -> Reservation | None:
    """Return a reservation only if the business is a party to it (strict isolation)."""
    return (
        _base_qs()
        .filter(Q(seller_business=business) | Q(requester_business=business))
        .filter(pk=reservation_id)
        .first()
    )
