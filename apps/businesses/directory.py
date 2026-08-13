"""The colleague directory — «لیست همکاران».

A colleague is a **Business**, not a manually created Contact. Every eligible
Business is in the directory automatically, so there is nothing to add, nothing
to keep in sync, and no way for two people at the same company to be two
different colleagues.

This replaces `apps.contacts`, where each business kept its own private,
hand-typed copy of the same list.
"""

from __future__ import annotations

from django.db.models import Q, QuerySet

from apps.core.persian import normalize_persian_text

from .models import Business, BusinessMembership


def colleague_businesses(viewer: Business) -> QuerySet[Business]:
    """Businesses ``viewer`` may see and trade with.

    A suspended Business neither sees the directory nor appears in it, matching
    the rule `inventory.policy` applies to the marketplace. Expiry is checked in
    Python rather than SQL because "no expiry set" is a null, and folding that
    into a query condition reads worse than it filters.
    """
    if viewer is None or viewer.status != Business.Status.ACTIVE:
        return Business.objects.none()

    return (
        Business.objects.filter(status=Business.Status.ACTIVE)
        .exclude(pk=viewer.pk)
        .order_by("name")
    )


def filter_colleagues(qs: QuerySet[Business], *, q: str = "") -> QuerySet[Business]:
    if not q:
        return qs
    term = normalize_persian_text(q)
    return qs.filter(
        Q(name__icontains=term)
        | Q(city__icontains=term)
        | Q(province__icontains=term)
        | Q(phone__icontains=term)
    )


def get_colleague(viewer: Business, business_id) -> Business | None:
    return colleague_businesses(viewer).filter(pk=business_id).first()


def representative_of(business: Business) -> BusinessMembership | None:
    """Who to ask for at this Business.

    The owner if there is one, otherwise the longest-standing active member.
    Deliberately not a configurable "primary contact" field: one more thing to
    fill in and keep current, for an answer that is almost always the owner.
    """
    memberships = (
        BusinessMembership.objects.filter(business=business, status=BusinessMembership.Status.ACTIVE)
        .select_related("user")
        .order_by("joined_at")
    )
    owner = next((m for m in memberships if m.role == BusinessMembership.Role.OWNER), None)
    return owner or memberships.first()
