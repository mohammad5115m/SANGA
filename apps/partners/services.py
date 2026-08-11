from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from apps.businesses.models import Business, BusinessMembership
from apps.businesses.permissions import PARTNERS_MANAGE
from apps.notifications.models import Notification
from apps.notifications.services import notify_user

from .models import PartnerRelation, SavedSearch, SupplierFollow

logger = logging.getLogger(__name__)


class PartnerError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def _require_partners_manage(membership: BusinessMembership) -> None:
    if membership is None or not membership.has_capability(PARTNERS_MANAGE):
        raise PartnerError("اجازه مدیریت شرکا را ندارید.")


@transaction.atomic
def request_partnership(
    *,
    partner_business: Business,
    supplier_business: Business,
    membership: BusinessMembership,
    message: str = "",
) -> PartnerRelation:
    if membership.business_id != partner_business.id:
        raise PartnerError("دسترسی نامعتبر است.")
    if partner_business.id == supplier_business.id:
        raise PartnerError("نمی‌توانید با کسب‌وکار خودتان همکاری ثبت کنید.")

    relation, created = PartnerRelation.objects.get_or_create(
        supplier_business=supplier_business,
        partner_business=partner_business,
        defaults={"status": PartnerRelation.Status.REQUESTED, "message": (message or "").strip()},
    )
    if not created:
        if relation.status == PartnerRelation.Status.BLOCKED:
            raise PartnerError("این رابطه مسدود شده است.")
        if relation.status == PartnerRelation.Status.APPROVED:
            raise PartnerError("همکاری از قبل تأیید شده است.")
        if relation.status == PartnerRelation.Status.REJECTED:
            relation.status = PartnerRelation.Status.REQUESTED
            relation.message = (message or "").strip()
            relation.decided_at = None
            relation.save(update_fields=["status", "message", "decided_at", "updated_at"])

    # Notify supplier owners/managers
    for m in supplier_business.memberships.filter(
        status=BusinessMembership.Status.ACTIVE,
        role__in=[BusinessMembership.Role.OWNER, BusinessMembership.Role.MANAGER],
    ):
        notify_user(
            user=m.user,
            business=supplier_business,
            kind=Notification.Kind.PARTNER_REQUEST,
            title="درخواست همکاری جدید",
            body=f"{partner_business.name} درخواست همکاری ارسال کرد.",
            link="/app/partners/incoming/",
        )
    return relation


@transaction.atomic
def decide_partnership(
    *,
    relation: PartnerRelation,
    membership: BusinessMembership,
    approve: bool,
) -> PartnerRelation:
    _require_partners_manage(membership)
    if relation.supplier_business_id != membership.business_id:
        raise PartnerError("فقط تأمین‌کننده می‌تواند این درخواست را بررسی کند.")
    if relation.status != PartnerRelation.Status.REQUESTED:
        raise PartnerError("این درخواست قابل تصمیم‌گیری نیست.")

    relation.status = PartnerRelation.Status.APPROVED if approve else PartnerRelation.Status.REJECTED
    relation.decided_at = timezone.now()
    relation.save(update_fields=["status", "decided_at", "updated_at"])

    for m in relation.partner_business.memberships.filter(status=BusinessMembership.Status.ACTIVE)[:5]:
        notify_user(
            user=m.user,
            business=relation.partner_business,
            kind=Notification.Kind.PARTNER_DECISION,
            title="نتیجه درخواست همکاری",
            body=(
                f"درخواست همکاری با {relation.supplier_business.name} "
                f"{'تأیید' if approve else 'رد'} شد."
            ),
            link="/app/marketplace/",
        )
    return relation


@transaction.atomic
def follow_supplier(
    *,
    follower_business: Business,
    supplier_business: Business,
    membership: BusinessMembership,
) -> SupplierFollow:
    if membership.business_id != follower_business.id:
        raise PartnerError("دسترسی نامعتبر است.")
    if follower_business.id == supplier_business.id:
        raise PartnerError("دنبال‌کردن خودتان ممکن نیست.")
    follow, _ = SupplierFollow.objects.get_or_create(
        follower_business=follower_business,
        supplier_business=supplier_business,
        defaults={"created_by": membership.user},
    )
    return follow


@transaction.atomic
def unfollow_supplier(*, follower_business: Business, supplier_business: Business) -> None:
    SupplierFollow.objects.filter(
        follower_business=follower_business,
        supplier_business=supplier_business,
    ).delete()


@transaction.atomic
def save_search(
    *,
    business: Business,
    user,
    name: str,
    query: dict,
    notify_enabled: bool = True,
) -> SavedSearch:
    name = (name or "").strip()
    if len(name) < 2:
        raise PartnerError("نام جستجو خیلی کوتاه است.")
    return SavedSearch.objects.create(
        business=business,
        user=user,
        name=name,
        query=query or {},
        notify_enabled=notify_enabled,
    )
