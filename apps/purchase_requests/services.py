from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.businesses.models import Business, BusinessMembership
from apps.businesses.permissions import RESERVATIONS_MANAGE
from apps.inventory.models import InventoryLot
from apps.matching.services import persist_matches
from apps.notifications.models import Notification
from apps.notifications.services import notify_user

from .models import PurchaseOffer, PurchaseRequest

logger = logging.getLogger(__name__)


class PurchaseRequestError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@transaction.atomic
def create_purchase_request(
    *,
    business: Business,
    membership: BusinessMembership,
    **fields,
) -> PurchaseRequest:
    if membership.business_id != business.id:
        raise PurchaseRequestError("دسترسی نامعتبر است.")
    title = (fields.get("title") or "").strip()
    if len(title) < 3:
        raise PurchaseRequestError("عنوان درخواست خیلی کوتاه است.")
    qty = fields.get("required_qty_sqm")
    if qty is None or Decimal(str(qty)) <= 0:
        raise PurchaseRequestError("متراژ مورد نیاز معتبر نیست.")

    pr = PurchaseRequest.objects.create(
        business=business,
        created_by=membership.user,
        title=title,
        stone_type=(fields.get("stone_type") or "").strip(),
        category=(fields.get("category") or "").strip(),
        color=(fields.get("color") or "").strip(),
        application=(fields.get("application") or "").strip(),
        required_qty_sqm=qty,
        thickness_mm=fields.get("thickness_mm"),
        length_cm=fields.get("length_cm"),
        width_cm=fields.get("width_cm"),
        acceptable_grade=(fields.get("acceptable_grade") or "").strip(),
        budget_amount=fields.get("budget_amount"),
        budget_currency=(fields.get("budget_currency") or "IRR"),
        destination_city=(fields.get("destination_city") or "").strip(),
        required_by=fields.get("required_by"),
        similar_accepted=bool(fields.get("similar_accepted", True)),
        notes=(fields.get("notes") or "").strip(),
        is_public_to_network=bool(fields.get("is_public_to_network", True)),
        status=PurchaseRequest.Status.OPEN,
    )
    persist_matches(pr)
    _notify_potential_sellers(pr)
    return pr


def _notify_potential_sellers(pr: PurchaseRequest) -> None:
    from apps.matching.models import MatchResult

    # Only notify for matches that have not been announced yet so re-running the
    # matcher (rematch) never sends duplicate notifications to the same sellers.
    pending = MatchResult.objects.filter(purchase_request=pr, notified=False)
    seller_ids = list(pending.values_list("lot__business_id", flat=True).distinct()[:20])
    for business_id in seller_ids:
        owners = BusinessMembership.objects.filter(
            business_id=business_id,
            status=BusinessMembership.Status.ACTIVE,
            role__in=[BusinessMembership.Role.OWNER, BusinessMembership.Role.MANAGER],
        ).select_related("user")[:3]
        for membership in owners:
            notify_user(
                user=membership.user,
                business=membership.business,
                kind=Notification.Kind.GENERAL,
                title="درخواست خرید منطبق",
                body=f"درخواست «{pr.title}» با موجودی شما هم‌خوانی دارد.",
                link=f"/app/purchase-requests/network/{pr.id}/",
            )
    pending.update(notified=True)


@transaction.atomic
def submit_private_offer(
    *,
    purchase_request: PurchaseRequest,
    seller_business: Business,
    membership: BusinessMembership,
    unit_price: Decimal,
    offered_qty_sqm: Decimal,
    message: str = "",
    lot: InventoryLot | None = None,
) -> PurchaseOffer:
    if membership.business_id != seller_business.id:
        raise PurchaseRequestError("دسترسی نامعتبر است.")
    if seller_business.id == purchase_request.business_id:
        raise PurchaseRequestError("نمی‌توانید روی درخواست خودتان پیشنهاد بدهید.")
    if purchase_request.status in {PurchaseRequest.Status.CLOSED, PurchaseRequest.Status.CANCELLED}:
        raise PurchaseRequestError("این درخواست بسته شده است.")
    if unit_price < 0 or offered_qty_sqm <= 0:
        raise PurchaseRequestError("مقادیر پیشنهاد معتبر نیست.")
    if lot is not None and lot.business_id != seller_business.id:
        raise PurchaseRequestError("محموله متعلق به شما نیست.")

    existing = PurchaseOffer.objects.filter(
        purchase_request=purchase_request,
        seller_business=seller_business,
        status=PurchaseOffer.Status.SUBMITTED,
    ).first()
    if existing:
        existing.unit_price = unit_price
        existing.offered_qty_sqm = offered_qty_sqm
        existing.message = (message or "").strip()
        existing.lot = lot
        existing.save()
        offer = existing
    else:
        offer = PurchaseOffer.objects.create(
            purchase_request=purchase_request,
            seller_business=seller_business,
            created_by=membership.user,
            lot=lot,
            unit_price=unit_price,
            offered_qty_sqm=offered_qty_sqm,
            message=(message or "").strip(),
            status=PurchaseOffer.Status.SUBMITTED,
        )

    if purchase_request.status in {PurchaseRequest.Status.OPEN, PurchaseRequest.Status.MATCHING}:
        purchase_request.status = PurchaseRequest.Status.OFFERED
        purchase_request.save(update_fields=["status", "updated_at"])

    for m in purchase_request.business.memberships.filter(
        status=BusinessMembership.Status.ACTIVE,
        role__in=[BusinessMembership.Role.OWNER, BusinessMembership.Role.MANAGER],
    )[:5]:
        notify_user(
            user=m.user,
            business=purchase_request.business,
            kind=Notification.Kind.GENERAL,
            title="پیشنهاد خصوصی جدید",
            body=f"{seller_business.name} روی «{purchase_request.title}» پیشنهاد داد.",
            link=f"/app/purchase-requests/{purchase_request.id}/",
        )
    return offer


@transaction.atomic
def decide_offer(
    *,
    offer: PurchaseOffer,
    membership: BusinessMembership,
    accept: bool,
) -> PurchaseOffer:
    pr = offer.purchase_request
    if membership.business_id != pr.business_id:
        raise PurchaseRequestError("فقط صاحب درخواست می‌تواند تصمیم بگیرد.")
    # Accepting an offer commits the buyer to a reservation, so it is a
    # reservation-management action and requires the capability.
    if accept and not membership.has_capability(RESERVATIONS_MANAGE):
        raise PurchaseRequestError("دسترسی لازم برای پذیرش پیشنهاد را ندارید.")
    if offer.status != PurchaseOffer.Status.SUBMITTED:
        raise PurchaseRequestError("این پیشنهاد قابل تصمیم‌گیری نیست.")

    offer.status = PurchaseOffer.Status.ACCEPTED if accept else PurchaseOffer.Status.REJECTED
    offer.save(update_fields=["status", "updated_at"])
    if accept:
        pr.status = PurchaseRequest.Status.CLOSED
        pr.closed_at = timezone.now()
        pr.save(update_fields=["status", "closed_at", "updated_at"])
        PurchaseOffer.objects.filter(
            purchase_request=pr,
            status=PurchaseOffer.Status.SUBMITTED,
        ).exclude(pk=offer.pk).update(status=PurchaseOffer.Status.REJECTED, updated_at=timezone.now())
        # Turn the accepted offer into an active reservation hold when it points
        # at a concrete lot. Runs in this same atomic block: if the lot lacks
        # quantity the whole acceptance rolls back.
        if offer.lot_id is not None:
            from apps.reservations.services import create_reservation_from_offer

            create_reservation_from_offer(offer=offer, membership=membership)
    return offer


@transaction.atomic
def close_purchase_request(*, purchase_request: PurchaseRequest, membership: BusinessMembership) -> PurchaseRequest:
    if membership.business_id != purchase_request.business_id:
        raise PurchaseRequestError("دسترسی ندارید.")
    purchase_request.status = PurchaseRequest.Status.CANCELLED
    purchase_request.closed_at = timezone.now()
    purchase_request.save(update_fields=["status", "closed_at", "updated_at"])
    return purchase_request
