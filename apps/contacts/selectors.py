from __future__ import annotations

from django.db.models import Q, QuerySet

from apps.businesses.models import Business
from apps.partners.models import PartnerRelation

from .models import Contact

# Relationship filter keys accepted by the list view.
KIND_FILTERS = {
    "customer": "is_customer",
    "supplier": "is_supplier",
    "trader": "is_trader",
}


def contacts_for_business(
    business: Business,
    *,
    q: str = "",
    kind: str = "",
    include_archived: bool = False,
) -> QuerySet[Contact]:
    """Tenant-scoped contact list with optional search and relationship filter."""
    qs = Contact.objects.filter(business=business)
    if not include_archived:
        qs = qs.filter(is_active=True)

    kind_field = KIND_FILTERS.get(kind)
    if kind_field:
        qs = qs.filter(**{kind_field: True})

    term = (q or "").strip()
    if term:
        qs = qs.filter(Q(display_name__icontains=term) | Q(phone__icontains=term))

    return qs.select_related("linked_business").order_by("display_name")


def get_contact(business: Business, contact_id) -> Contact:
    """Fetch a single contact, enforcing tenant ownership.

    Raises ``Contact.DoesNotExist`` if the contact belongs to another business.
    """
    return Contact.objects.select_related("linked_business", "business").get(
        pk=contact_id,
        business=business,
    )


def approved_partner_businesses(business: Business) -> QuerySet[Business]:
    """Businesses that have an approved partner relation with ``business``
    (in either direction). Used to constrain the optional contact link so a
    contact can only be tied to a genuinely approved partner.
    """
    as_partner = PartnerRelation.objects.filter(
        partner_business=business,
        status=PartnerRelation.Status.APPROVED,
    ).values_list("supplier_business_id", flat=True)
    as_supplier = PartnerRelation.objects.filter(
        supplier_business=business,
        status=PartnerRelation.Status.APPROVED,
    ).values_list("partner_business_id", flat=True)
    ids = set(as_partner) | set(as_supplier)
    return Business.objects.filter(id__in=ids).order_by("name")


def is_approved_partner(business: Business, other: Business) -> bool:
    if other is None or other.id == business.id:
        return False
    return PartnerRelation.objects.filter(
        Q(
            partner_business=business,
            supplier_business=other,
        )
        | Q(
            supplier_business=business,
            partner_business=other,
        ),
        status=PartnerRelation.Status.APPROVED,
    ).exists()
