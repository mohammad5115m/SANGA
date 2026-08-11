from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.businesses.models import Business, BusinessMembership
from apps.businesses.permissions import RESERVATIONS_MANAGE
from apps.inventory.models import InventoryLot
from apps.notifications.models import Notification
from apps.notifications.services import notify_user

from .models import Reservation

logger = logging.getLogger(__name__)

DEFAULT_HOLD_HOURS = 48

# Explicit, enforced state machine. Extend keeps the reservation APPROVED but is
# a distinct action, so APPROVED -> APPROVED is permitted.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    Reservation.Status.REQUESTED: {
        Reservation.Status.APPROVED,
        Reservation.Status.REJECTED,
        Reservation.Status.CANCELLED,
    },
    Reservation.Status.APPROVED: {
        Reservation.Status.APPROVED,
        Reservation.Status.CANCELLED,
        Reservation.Status.EXPIRED,
        Reservation.Status.CONVERTED,
    },
    Reservation.Status.REJECTED: set(),
    Reservation.Status.CANCELLED: set(),
    Reservation.Status.EXPIRED: set(),
    Reservation.Status.CONVERTED: set(),
}


class ReservationError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def _require_manage(membership: BusinessMembership | None) -> None:
    if membership is None or not membership.has_capability(RESERVATIONS_MANAGE):
        raise ReservationError("دسترسی لازم برای مدیریت رزرو را ندارید.")


def _ensure_transition(reservation: Reservation, new_status: str) -> None:
    if new_status not in ALLOWED_TRANSITIONS.get(reservation.status, set()):
        raise ReservationError("این تغییر وضعیت برای رزرو مجاز نیست.")


def _hold_hours(business: Business) -> int:
    try:
        value = int((business.settings or {}).get("reservation_hold_hours", DEFAULT_HOLD_HOURS))
        return value if value > 0 else DEFAULT_HOLD_HOURS
    except (TypeError, ValueError):
        return DEFAULT_HOLD_HOURS


def _notify_business_managers(business: Business, *, kind: str, title: str, body: str, link: str = "") -> None:
    memberships = BusinessMembership.objects.filter(
        business=business,
        status=BusinessMembership.Status.ACTIVE,
        role__in=[BusinessMembership.Role.OWNER, BusinessMembership.Role.MANAGER],
    ).select_related("user")[:5]
    for membership in memberships:
        notify_user(
            user=membership.user,
            business=business,
            kind=kind,
            title=title,
            body=body,
            link=link,
        )


def _detail_link(reservation: Reservation) -> str:
    return f"/app/reservations/{reservation.id}/"


def _lock_and_reserve(lot_id, quantity: Decimal) -> InventoryLot:
    """Lock a lot row and deduct quantity, raising if insufficient."""
    lot = InventoryLot.objects.select_for_update().get(pk=lot_id)
    if lot.available_sqm < quantity:
        raise ReservationError("موجودی کافی برای این رزرو وجود ندارد.")
    lot.available_sqm = lot.available_sqm - quantity
    if lot.available_sqm <= 0:
        lot.status = InventoryLot.Status.RESERVED
    lot.save(update_fields=["available_sqm", "status", "updated_at"])
    return lot


def _release_quantity(reservation: Reservation) -> None:
    """Return reserved quantity to the lot. Idempotent via ``released_at``."""
    res = Reservation.objects.select_for_update().get(pk=reservation.pk)
    if res.released_at is not None:
        return
    lot = InventoryLot.objects.select_for_update().get(pk=res.lot_id)
    lot.available_sqm = lot.available_sqm + res.quantity_sqm
    if lot.status == InventoryLot.Status.RESERVED:
        lot.status = InventoryLot.Status.AVAILABLE
    lot.save(update_fields=["available_sqm", "status", "updated_at"])
    res.released_at = timezone.now()
    res.save(update_fields=["released_at", "updated_at"])
    reservation.released_at = res.released_at


@transaction.atomic
def request_reservation(
    *,
    lot: InventoryLot,
    requester_business: Business,
    membership: BusinessMembership,
    quantity_sqm: Decimal,
    notes: str = "",
) -> Reservation:
    _require_manage(membership)
    if membership.business_id != requester_business.id:
        raise ReservationError("دسترسی نامعتبر است.")
    if lot.business_id == requester_business.id:
        raise ReservationError("نمی‌توانید محموله خودتان را رزرو کنید.")
    qty = Decimal(str(quantity_sqm))
    if qty <= 0:
        raise ReservationError("متراژ رزرو معتبر نیست.")
    if qty > lot.available_sqm:
        raise ReservationError("متراژ درخواستی از موجودی قابل دسترس بیشتر است.")

    reservation = Reservation.objects.create(
        lot=lot,
        seller_business=lot.business,
        requester_business=requester_business,
        requested_by=membership.user,
        quantity_sqm=qty,
        status=Reservation.Status.REQUESTED,
        notes=(notes or "").strip(),
    )
    _notify_business_managers(
        lot.business,
        kind=Notification.Kind.RESERVATION_REQUEST,
        title="درخواست رزرو جدید",
        body=f"{requester_business.name} برای «{lot.lot_code}» درخواست رزرو {qty} m² داد.",
        link=_detail_link(reservation),
    )
    return reservation


@transaction.atomic
def approve_reservation(*, reservation: Reservation, membership: BusinessMembership) -> Reservation:
    _require_manage(membership)
    if membership.business_id != reservation.seller_business_id:
        raise ReservationError("فقط فروشنده می‌تواند رزرو را تأیید کند.")
    # Approve only from REQUESTED: APPROVED -> APPROVED is reserved for extend and
    # must never re-deduct quantity.
    if reservation.status != Reservation.Status.REQUESTED:
        raise ReservationError("این رزرو قابل تأیید نیست.")

    _lock_and_reserve(reservation.lot_id, reservation.quantity_sqm)
    reservation.status = Reservation.Status.APPROVED
    reservation.expires_at = timezone.now() + timedelta(hours=_hold_hours(reservation.seller_business))
    reservation.decided_by = membership.user
    reservation.decided_at = timezone.now()
    reservation.released_at = None
    reservation.save(
        update_fields=["status", "expires_at", "decided_by", "decided_at", "released_at", "updated_at"]
    )
    _notify_business_managers(
        reservation.requester_business,
        kind=Notification.Kind.RESERVATION_DECISION,
        title="رزرو تأیید شد",
        body=f"رزرو شما روی «{reservation.lot.lot_code}» تأیید شد.",
        link=_detail_link(reservation),
    )
    return reservation


@transaction.atomic
def reject_reservation(
    *, reservation: Reservation, membership: BusinessMembership, reason: str = ""
) -> Reservation:
    _require_manage(membership)
    if membership.business_id != reservation.seller_business_id:
        raise ReservationError("فقط فروشنده می‌تواند رزرو را رد کند.")
    _ensure_transition(reservation, Reservation.Status.REJECTED)

    reservation.status = Reservation.Status.REJECTED
    reservation.reason = (reason or "").strip()
    reservation.decided_by = membership.user
    reservation.decided_at = timezone.now()
    reservation.save(update_fields=["status", "reason", "decided_by", "decided_at", "updated_at"])
    _notify_business_managers(
        reservation.requester_business,
        kind=Notification.Kind.RESERVATION_DECISION,
        title="رزرو رد شد",
        body=f"رزرو شما روی «{reservation.lot.lot_code}» رد شد.",
        link=_detail_link(reservation),
    )
    return reservation


@transaction.atomic
def extend_reservation(
    *, reservation: Reservation, membership: BusinessMembership, hours: int
) -> Reservation:
    _require_manage(membership)
    if membership.business_id != reservation.seller_business_id:
        raise ReservationError("فقط فروشنده می‌تواند رزرو را تمدید کند.")
    if reservation.status != Reservation.Status.APPROVED:
        raise ReservationError("فقط رزرو فعال قابل تمدید است.")
    try:
        hours_int = int(hours)
    except (TypeError, ValueError) as exc:
        raise ReservationError("مدت تمدید معتبر نیست.") from exc
    if hours_int <= 0:
        raise ReservationError("مدت تمدید باید بزرگ‌تر از صفر باشد.")

    base = reservation.expires_at or timezone.now()
    reservation.expires_at = base + timedelta(hours=hours_int)
    reservation.extended_count = reservation.extended_count + 1
    reservation.save(update_fields=["expires_at", "extended_count", "updated_at"])
    _notify_business_managers(
        reservation.requester_business,
        kind=Notification.Kind.RESERVATION_DECISION,
        title="رزرو تمدید شد",
        body=f"مهلت رزرو شما روی «{reservation.lot.lot_code}» تمدید شد.",
        link=_detail_link(reservation),
    )
    return reservation


@transaction.atomic
def cancel_reservation(
    *, reservation: Reservation, membership: BusinessMembership, reason: str = ""
) -> Reservation:
    _require_manage(membership)
    if membership.business_id not in {reservation.seller_business_id, reservation.requester_business_id}:
        raise ReservationError("به این رزرو دسترسی ندارید.")
    _ensure_transition(reservation, Reservation.Status.CANCELLED)

    was_active = reservation.status == Reservation.Status.APPROVED
    reservation.status = Reservation.Status.CANCELLED
    reservation.reason = (reason or "").strip()
    reservation.decided_by = membership.user
    reservation.decided_at = timezone.now()
    reservation.save(update_fields=["status", "reason", "decided_by", "decided_at", "updated_at"])
    if was_active:
        _release_quantity(reservation)

    other = (
        reservation.requester_business
        if membership.business_id == reservation.seller_business_id
        else reservation.seller_business
    )
    _notify_business_managers(
        other,
        kind=Notification.Kind.RESERVATION_DECISION,
        title="رزرو لغو شد",
        body=f"رزرو روی «{reservation.lot.lot_code}» لغو شد.",
        link=_detail_link(reservation),
    )
    return reservation


@transaction.atomic
def convert_reservation(*, reservation: Reservation, membership: BusinessMembership) -> Reservation:
    _require_manage(membership)
    if membership.business_id != reservation.seller_business_id:
        raise ReservationError("فقط فروشنده می‌تواند رزرو را نهایی کند.")
    _ensure_transition(reservation, Reservation.Status.CONVERTED)

    # Quantity was already deducted at approval; converting marks the sale.
    lot = InventoryLot.objects.select_for_update().get(pk=reservation.lot_id)
    if lot.available_sqm <= 0:
        lot.status = InventoryLot.Status.SOLD
    else:
        lot.status = InventoryLot.Status.PARTIALLY_SOLD
    lot.save(update_fields=["status", "updated_at"])

    reservation.status = Reservation.Status.CONVERTED
    reservation.decided_by = membership.user
    reservation.decided_at = timezone.now()
    reservation.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])
    _notify_business_managers(
        reservation.requester_business,
        kind=Notification.Kind.RESERVATION_DECISION,
        title="رزرو به فروش تبدیل شد",
        body=f"رزرو شما روی «{reservation.lot.lot_code}» نهایی شد.",
        link=_detail_link(reservation),
    )
    return reservation


@transaction.atomic
def create_reservation_from_offer(
    *, offer, membership: BusinessMembership
) -> Reservation | None:
    """Create an auto-approved hold from an accepted private offer.

    Called inside ``decide_offer``'s atomic block. The buyer (PR owner) has
    already passed the reservation-management check; acceptance is a mutual
    agreement, so the seller's lot is locked immediately.
    """
    lot = offer.lot
    if lot is None:
        return None
    purchase_request = offer.purchase_request
    requester_business = purchase_request.business
    seller_business = offer.seller_business
    qty = Decimal(str(offer.offered_qty_sqm))

    _lock_and_reserve(lot.pk, qty)
    reservation = Reservation.objects.create(
        lot=lot,
        seller_business=seller_business,
        requester_business=requester_business,
        requested_by=membership.user,
        decided_by=membership.user,
        source_offer=offer,
        quantity_sqm=qty,
        status=Reservation.Status.APPROVED,
        expires_at=timezone.now() + timedelta(hours=_hold_hours(seller_business)),
        decided_at=timezone.now(),
        reason="ایجادشده از پیشنهاد پذیرفته‌شده",
    )
    _notify_business_managers(
        seller_business,
        kind=Notification.Kind.RESERVATION_DECISION,
        title="پیشنهاد شما پذیرفته شد",
        body=f"پیشنهاد شما پذیرفته و رزرو {qty} m² روی «{lot.lot_code}» ایجاد شد.",
        link=_detail_link(reservation),
    )
    return reservation


def expire_due_reservations() -> dict[str, int]:
    """Expire approved reservations past their hold. Idempotent and safe to rerun."""
    now = timezone.now()
    due_ids = list(
        Reservation.objects.filter(
            status=Reservation.Status.APPROVED,
            expires_at__lte=now,
        ).values_list("pk", flat=True)
    )
    expired = 0
    for pk in due_ids:
        with transaction.atomic():
            reservation = Reservation.objects.select_for_update().get(pk=pk)
            if reservation.status != Reservation.Status.APPROVED:
                continue
            if reservation.expires_at is None or reservation.expires_at > timezone.now():
                continue
            reservation.status = Reservation.Status.EXPIRED
            reservation.save(update_fields=["status", "updated_at"])
            _release_quantity(reservation)
            for business in (reservation.seller_business, reservation.requester_business):
                _notify_business_managers(
                    business,
                    kind=Notification.Kind.RESERVATION_EXPIRED,
                    title="رزرو منقضی شد",
                    body=f"رزرو روی «{reservation.lot.lot_code}» منقضی و متراژ آزاد شد.",
                    link=_detail_link(reservation),
                )
            expired += 1
    logger.info("Reservation expiry scanned=%s expired=%s", len(due_ids), expired)
    return {"scanned": len(due_ids), "expired": expired}
