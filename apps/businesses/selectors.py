from __future__ import annotations

from django.db.models import QuerySet

from apps.accounts.models import User

from .models import BusinessMembership


def memberships_for_user(user: User) -> QuerySet[BusinessMembership]:
    if not user.is_authenticated:
        return BusinessMembership.objects.none()
    return (
        BusinessMembership.objects.filter(user=user, status=BusinessMembership.Status.ACTIVE)
        .select_related("business")
        .order_by("business__name")
    )


def get_active_membership(user: User, business_id: str | None) -> BusinessMembership | None:
    qs = memberships_for_user(user)
    if business_id:
        return qs.filter(business_id=business_id).first()
    return qs.first()
