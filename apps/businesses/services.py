from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User

from .models import Business, BusinessMembership, Warehouse
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
    logger.info("Business created id=%s owner=%s", business.id, owner.id)
    return business


@transaction.atomic
def add_warehouse(
    *,
    business: Business,
    name: str,
    city: str = "",
    address: str = "",
    is_default: bool = False,
) -> Warehouse:
    name = (name or "").strip()
    if not name:
        raise BusinessServiceError("نام انبار الزامی است.")
    if Warehouse.objects.filter(business=business, name=name).exists():
        raise BusinessServiceError("انباری با این نام از قبل وجود دارد.")

    make_default = is_default or not Warehouse.objects.filter(business=business).exists()
    warehouse = Warehouse.objects.create(
        business=business,
        name=name,
        city=city.strip() or business.city,
        address=address.strip(),
        is_default=make_default,
    )
    if business.onboarding_step < 3:
        business.onboarding_step = 3
        business.save(update_fields=["onboarding_step", "updated_at"])
    return warehouse


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


def invite_member(
    *,
    business: Business,
    actor_membership: BusinessMembership,
    user: User,
    role: str = BusinessMembership.Role.STAFF,
) -> BusinessMembership:
    if not actor_membership.has_capability(TEAM_MANAGE):
        raise BusinessServiceError("اجازه مدیریت تیم را ندارید.")
    membership, created = BusinessMembership.objects.get_or_create(
        user=user,
        business=business,
        defaults={
            "role": role,
            "permissions": defaults_for_role(role),
            "status": BusinessMembership.Status.ACTIVE,
        },
    )
    if not created and membership.status == BusinessMembership.Status.SUSPENDED:
        membership.status = BusinessMembership.Status.ACTIVE
        membership.role = role
        membership.permissions = defaults_for_role(role)
        membership.save()
    return membership
