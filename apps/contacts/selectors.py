from __future__ import annotations

from django.db.models import Q, QuerySet

from apps.businesses.models import Business

from .models import Contact

def contacts_for_business(
    business: Business,
    *,
    q: str = "",
    include_archived: bool = False,
) -> QuerySet[Contact]:
    """Tenant-scoped contact list with optional free-text search.

    Every contact is a colleague, so there is nothing to filter by type.
    """
    qs = Contact.objects.filter(business=business)
    if not include_archived:
        qs = qs.filter(is_active=True)

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


def linkable_businesses(business: Business) -> QuerySet[Business]:
    """Businesses a contact may be linked to.

    There is no partnership to approve any more: every other active business is
    a colleague, so the only exclusion is the acting business itself.
    """
    return (
        Business.objects.filter(status=Business.Status.ACTIVE)
        .exclude(pk=business.pk)
        .order_by("name")
    )


def is_linkable_business(business: Business, other: Business) -> bool:
    if other is None or other.id == business.id:
        return False
    return other.status == Business.Status.ACTIVE
