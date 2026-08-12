from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User

from .entitlements import EntitlementError, require_seat_available
from .models import Business, BusinessMembership
from .permissions import BUSINESS_SETTINGS, TEAM_MANAGE, defaults_for_role

logger = logging.getLogger(__name__)


class BusinessServiceError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@transaction.atomic
def create_business_for_owner(
    *,
    owner: User,
    name: str,
    city: str = "",
    province: str = "",
    phone: str = "",
) -> Business:
    """Provision a Business and make ``owner`` its Owner.

    Platform-admin only. There is no public route into this function: SANGA has
    no self-service signup, so a Business exists only because an operator ran
    the ``provision_business`` command or used Django admin. Callers are
    responsible for having established that authority — the function itself
    cannot see a request.
    """
    name = (name or "").strip()
    if len(name) < 2:
        raise BusinessServiceError("نام کسب‌وکار خیلی کوتاه است.")

    business = Business.objects.create(
        name=name,
        city=city.strip(),
        province=province.strip(),
        phone=phone.strip() or owner.phone,
        onboarding_step=2,
    )
    BusinessMembership.objects.create(
        user=owner,
        business=business,
        role=BusinessMembership.Role.OWNER,
        permissions=defaults_for_role(BusinessMembership.Role.OWNER),
        status=BusinessMembership.Status.ACTIVE,
    )
    logger.info("Business provisioned id=%s owner=%s", business.id, owner.id)
    return business


def update_business_profile(
    *,
    business: Business,
    actor_membership: BusinessMembership,
    **fields: str,
) -> Business:
    if not actor_membership.has_capability(BUSINESS_SETTINGS):
        raise BusinessServiceError("اجازه ویرایش تنظیمات کسب‌وکار را ندارید.")
    for key in ("name", "city", "province", "phone", "address", "website"):
        if key in fields and fields[key] is not None:
            setattr(business, key, str(fields[key]).strip())
    if business.onboarding_step < 2:
        business.onboarding_step = 2
    business.save()
    return business


def complete_onboarding(business: Business) -> Business:
    business.onboarding_completed_at = timezone.now()
    business.onboarding_step = 99
    business.save(update_fields=["onboarding_completed_at", "onboarding_step", "updated_at"])
    return business


@transaction.atomic
def invite_member(
    *,
    business: Business,
    actor_membership: BusinessMembership,
    user: User,
    role: str = BusinessMembership.Role.STAFF,
) -> BusinessMembership:
    """Add or reactivate a member, within the Business's seat limit.

    The seat check happens here rather than at login: lowering a limit must not
    lock out people who are already working, it should bite the next time
    somebody is added.
    """
    if not actor_membership.has_capability(TEAM_MANAGE):
        raise BusinessServiceError("اجازه مدیریت تیم را ندارید.")

    existing = BusinessMembership.objects.filter(user=user, business=business).first()
    if existing is not None and existing.status == BusinessMembership.Status.ACTIVE:
        return existing

    try:
        require_seat_available(business)
    except EntitlementError as exc:
        raise BusinessServiceError(exc.message) from exc

    if existing is not None:
        existing.status = BusinessMembership.Status.ACTIVE
        existing.role = role
        existing.permissions = defaults_for_role(role)
        existing.save()
        return existing

    return BusinessMembership.objects.create(
        user=user,
        business=business,
        role=role,
        permissions=defaults_for_role(role),
        status=BusinessMembership.Status.ACTIVE,
    )
