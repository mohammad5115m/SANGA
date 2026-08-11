from __future__ import annotations

from django.db.models import QuerySet

from .models import ContactPrice


def contact_prices_for_lot(business, lot) -> QuerySet[ContactPrice]:
    """Overrides set on ``lot`` by ``business``, for the management screen.

    Scoped by both the lot's owner and the contact's owner, so a lot id from
    another tenant yields nothing rather than that tenant's price list.
    """
    return (
        ContactPrice.objects.filter(
            lot=lot,
            lot__business=business,
            contact__business=business,
        )
        .select_related("contact", "contact__linked_business", "created_by")
        .order_by("contact__display_name")
    )


def contact_prices_for_contact(business, contact) -> QuerySet[ContactPrice]:
    """Overrides granted to one contact, for the read-only contact detail panel."""
    return (
        ContactPrice.objects.filter(
            contact=contact,
            contact__business=business,
            lot__business=business,
        )
        .select_related("lot", "lot__product")
        .order_by("lot__lot_code")
    )
