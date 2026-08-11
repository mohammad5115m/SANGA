from __future__ import annotations

import logging

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounting.selectors import trade_entry_for_reservation
from apps.businesses.decorators import business_login_required, require_capability
from apps.businesses.permissions import (
    LEDGER_MANAGE,
    RESERVATIONS_MANAGE,
    RESERVATIONS_VIEW,
)
from apps.marketplace.selectors import get_marketplace_lot

from .forms import ExtendReservationForm, ReservationRequestForm
from .models import Reservation
from .selectors import (
    get_reservation_for_business,
    incoming_reservations,
    outgoing_reservations,
)
from .services import (
    ReservationError,
    approve_reservation,
    cancel_reservation,
    convert_reservation,
    extend_reservation,
    reject_reservation,
    request_reservation,
)

logger = logging.getLogger(__name__)


@business_login_required
@require_capability(RESERVATIONS_VIEW)
def inbox(request: HttpRequest) -> HttpResponse:
    items = incoming_reservations(request.business)
    return render(request, "reservations/inbox.html", {"items": items})


@business_login_required
@require_capability(RESERVATIONS_VIEW)
def my_list(request: HttpRequest) -> HttpResponse:
    items = outgoing_reservations(request.business)
    return render(request, "reservations/my_list.html", {"items": items})


@business_login_required
@require_capability(RESERVATIONS_VIEW)
def detail(request: HttpRequest, reservation_id) -> HttpResponse:
    reservation = get_reservation_for_business(request.business, reservation_id)
    if reservation is None:
        messages.error(request, "رزرو یافت نشد.")
        return redirect("reservations:inbox")
    is_seller = reservation.seller_business_id == request.business.id
    # The financial step is the seller's, is separate from conversion, and is
    # offered only while it can still be done exactly once.
    trade_entry = trade_entry_for_reservation(request.business, reservation) if is_seller else None
    can_record_trade = (
        is_seller
        and trade_entry is None
        and reservation.status == Reservation.Status.CONVERTED
        and request.membership.has_capability(LEDGER_MANAGE)
    )
    return render(
        request,
        "reservations/detail.html",
        {
            "reservation": reservation,
            "is_seller": is_seller,
            "extend_form": ExtendReservationForm(),
            "trade_entry": trade_entry,
            "can_record_trade": can_record_trade,
        },
    )


@business_login_required
@require_capability(RESERVATIONS_VIEW)
@require_http_methods(["GET", "POST"])
def request_create(request: HttpRequest, lot_id) -> HttpResponse:
    lot = get_marketplace_lot(request.business, lot_id)
    if lot is None:
        messages.error(request, "این محموله برای رزرو در دسترس نیست.")
        return redirect("marketplace:home")

    form = ReservationRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            reservation = request_reservation(
                lot=lot,
                requester_business=request.business,
                membership=request.membership,
                quantity_sqm=form.cleaned_data["quantity_sqm"],
                notes=form.cleaned_data.get("notes", ""),
            )
        except ReservationError as exc:
            form.add_error(None, exc.message)
        except Exception:
            logger.exception("Reservation request failed")
            form.add_error(None, "ثبت درخواست رزرو با خطا روبه‌رو شد.")
        else:
            messages.success(request, "درخواست رزرو برای تأمین‌کننده ارسال شد.")
            return redirect("reservations:detail", reservation_id=reservation.id)
    return render(request, "reservations/request_form.html", {"form": form, "lot": lot})


def _load_for_action(request: HttpRequest, reservation_id):
    reservation = get_reservation_for_business(request.business, reservation_id)
    if reservation is None:
        messages.error(request, "رزرو یافت نشد.")
    return reservation


@business_login_required
@require_capability(RESERVATIONS_MANAGE)
@require_POST
def approve(request: HttpRequest, reservation_id) -> HttpResponse:
    reservation = _load_for_action(request, reservation_id)
    if reservation is None:
        return redirect("reservations:inbox")
    try:
        approve_reservation(reservation=reservation, membership=request.membership)
    except ReservationError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "رزرو تأیید شد.")
    return redirect("reservations:detail", reservation_id=reservation.id)


@business_login_required
@require_capability(RESERVATIONS_MANAGE)
@require_POST
def reject(request: HttpRequest, reservation_id) -> HttpResponse:
    reservation = _load_for_action(request, reservation_id)
    if reservation is None:
        return redirect("reservations:inbox")
    try:
        reject_reservation(
            reservation=reservation,
            membership=request.membership,
            reason=request.POST.get("reason", ""),
        )
    except ReservationError as exc:
        messages.error(request, exc.message)
    else:
        messages.info(request, "رزرو رد شد.")
    return redirect("reservations:detail", reservation_id=reservation.id)


@business_login_required
@require_capability(RESERVATIONS_MANAGE)
@require_POST
def extend(request: HttpRequest, reservation_id) -> HttpResponse:
    reservation = _load_for_action(request, reservation_id)
    if reservation is None:
        return redirect("reservations:inbox")
    form = ExtendReservationForm(request.POST)
    if not form.is_valid():
        messages.error(request, "مدت تمدید معتبر نیست.")
        return redirect("reservations:detail", reservation_id=reservation.id)
    try:
        extend_reservation(
            reservation=reservation,
            membership=request.membership,
            hours=form.cleaned_data["hours"],
        )
    except ReservationError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "رزرو تمدید شد.")
    return redirect("reservations:detail", reservation_id=reservation.id)


@business_login_required
@require_capability(RESERVATIONS_MANAGE)
@require_POST
def cancel(request: HttpRequest, reservation_id) -> HttpResponse:
    reservation = _load_for_action(request, reservation_id)
    if reservation is None:
        return redirect("reservations:inbox")
    try:
        cancel_reservation(
            reservation=reservation,
            membership=request.membership,
            reason=request.POST.get("reason", ""),
        )
    except ReservationError as exc:
        messages.error(request, exc.message)
    else:
        messages.info(request, "رزرو لغو شد.")
    return redirect("reservations:detail", reservation_id=reservation.id)


@business_login_required
@require_capability(RESERVATIONS_MANAGE)
@require_POST
def convert(request: HttpRequest, reservation_id) -> HttpResponse:
    reservation = _load_for_action(request, reservation_id)
    if reservation is None:
        return redirect("reservations:inbox")
    try:
        convert_reservation(reservation=reservation, membership=request.membership)
    except ReservationError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "رزرو به فروش تبدیل شد.")
    return redirect("reservations:detail", reservation_id=reservation.id)
