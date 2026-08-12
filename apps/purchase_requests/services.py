from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.businesses.models import Business, BusinessMembership
from apps.businesses.permissions import LEADS_MANAGE
from apps.inventory.models import InventoryLot
from apps.notifications.models import Notification
from apps.notifications.services import notify_user

from .models import PurchaseOffer, PurchaseRequest

logger = logging.getLogger(__name__)


class PurchaseRequestError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def _require_respond(membership: BusinessMembership | None, message: str) -> None:
    """Posting demand, quoting on it, and accepting a quote are all the same
    kind of act — committing the business to a counterparty — so they share the
    existing ``leads.manage`` capability. Viewers, who may browse the demand
    board, are excluded.
    """
    if membership is None or not membership.has_capability(LEADS_MANAGE):
        raise PurchaseRequestError(message)


def _require_active_network_business(business: Business | None, message: str) -> None:
    """Acting on someone else's demand — quoting on it, or deciding on a quote —
    needs an active business on both sides, the same rule the demand board in
    ``selectors.network_purchase_requests`` and ``marketplace_lots_for`` apply.
    Same notion of "active" as ``contacts.is_linkable_business``. This never
    touches a business's own requests, inventory or ledger.
    """
    if business is None or business.status != Business.Status.ACTIVE:
        raise PurchaseRequestError(message)


@transaction.atomic
def create_purchase_request(
    *,
    business: Business,
    membership: BusinessMembership,
    **fields,
) -> PurchaseRequest:
    if membership.business_id != business.id:
        raise PurchaseRequestError("دسترسی نامعتبر است.")
    _require_respond(membership, "اجازه ثبت درخواست خرید را ندارید.")
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
    # Nothing is pushed at sellers any more: automatic matching is gone, so demand
    # is found by browsing the network board rather than by a scoring rule.
    return pr


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
    _require_respond(membership, "اجازه ارسال پیشنهاد را ندارید.")
    _require_active_network_business(
        seller_business,
        "کسب‌وکار شما فعال نیست و امکان ارسال پیشنهاد در شبکه را ندارید.",
    )
    _require_active_network_business(
        purchase_request.business,
        "این درخواست در شبکه فعال نیست.",
    )
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
    # Deciding on an offer is part of running your own purchase request, so it
    # takes the same capability as creating one.
    _require_respond(membership, "اجازه تصمیم‌گیری درباره پیشنهاد را ندارید.")
    # Accepting or rejecting commits the two businesses to each other, so it is
    # a network act and needs both sides active — even though the offer itself
    # was submitted while they still were.
    _require_active_network_business(
        pr.business,
        "کسب‌وکار شما فعال نیست و امکان تصمیم‌گیری درباره پیشنهاد را ندارید.",
    )
    _require_active_network_business(
        offer.seller_business,
        "کسب‌وکار پیشنهاددهنده فعال نیست.",
    )
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
    # Acceptance no longer holds stock: nothing is reserved and no quantity moves.
    # It records the decision and tells the seller, who then settles the trade
    # offline and records it in the ledger.
    _notify_offer_decision(offer, accept=accept)
    return offer


def _notify_offer_decision(offer: PurchaseOffer, *, accept: bool) -> None:
    title = "پیشنهاد شما پذیرفته شد" if accept else "پیشنهاد شما رد شد"
    verb = "پذیرفت" if accept else "رد کرد"
    body = f"{offer.purchase_request.business.name} پیشنهاد شما روی «{offer.purchase_request.title}» را {verb}."
    for m in offer.seller_business.memberships.filter(
        status=BusinessMembership.Status.ACTIVE,
        role__in=[BusinessMembership.Role.OWNER, BusinessMembership.Role.MANAGER],
    ).select_related("user")[:5]:
        notify_user(
            user=m.user,
            business=offer.seller_business,
            kind=Notification.Kind.GENERAL,
            title=title,
            body=body,
            link=f"/app/purchase-requests/network/{offer.purchase_request_id}/",
        )


@transaction.atomic
def close_purchase_request(*, purchase_request: PurchaseRequest, membership: BusinessMembership) -> PurchaseRequest:
    if membership.business_id != purchase_request.business_id:
        raise PurchaseRequestError("دسترسی ندارید.")
    _require_respond(membership, "اجازه لغو درخواست خرید را ندارید.")
    purchase_request.status = PurchaseRequest.Status.CANCELLED
    purchase_request.closed_at = timezone.now()
    purchase_request.save(update_fields=["status", "closed_at", "updated_at"])
    return purchase_request
