from __future__ import annotations

import logging
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_http_methods

from apps.businesses.decorators import business_login_required, require_capability
from apps.businesses.permissions import LEDGER_MANAGE, LEDGER_VIEW
from apps.contacts.models import Contact
from apps.contacts.selectors import get_contact, is_approved_partner
from apps.contacts.services import ContactError, create_contact
from apps.reservations.models import Reservation
from apps.reservations.selectors import get_reservation_for_business

from .forms import LedgerEntryForm, QuickContactForm, TradeEntryForm
from .models import LedgerEntry
from .selectors import (
    contact_balances,
    contact_statement,
    current_balance,
    describe_balance,
    reversed_entry_ids,
    suggested_contact_for_reservation,
    suggested_trade_amount,
    trade_entry_for_reservation,
)
from .services import (
    TRADE_ALREADY_RECORDED,
    LedgerDuplicateError,
    LedgerError,
    post_entry,
    post_trade_entry,
    reverse_entry,
)

logger = logging.getLogger(__name__)


def _get_owned_contact(request: HttpRequest, contact_id) -> Contact:
    try:
        return get_contact(request.business, contact_id)
    except Contact.DoesNotExist as exc:
        raise Http404("مخاطب یافت نشد.") from exc


def _parse_date(value: str):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return parse_date(value)
    except ValueError:
        return None


@business_login_required
@require_capability(LEDGER_VIEW)
def ledger_index(request: HttpRequest) -> HttpResponse:
    """Entry point for the ledger: every contact with its current balance."""
    rows = [
        {"contact": contact, "balance": describe_balance(contact.balance)}
        for contact in contact_balances(request.business)
    ]
    return render(
        request,
        "accounting/index.html",
        {"rows": rows, "can_manage": request.membership.has_capability(LEDGER_MANAGE)},
    )


@business_login_required
@require_capability(LEDGER_VIEW)
def statement(request: HttpRequest, contact_id) -> HttpResponse:
    contact = _get_owned_contact(request, contact_id)
    date_from = _parse_date(request.GET.get("from", ""))
    date_to = _parse_date(request.GET.get("to", ""))
    entry_type = request.GET.get("type", "").strip()

    entries = contact_statement(
        request.business,
        contact,
        date_from=date_from,
        date_to=date_to,
        entry_type=entry_type,
    )
    balance = current_balance(request.business, contact)
    reversed_ids = reversed_entry_ids(request.business, contact)
    can_manage = request.membership.has_capability(LEDGER_MANAGE)

    return render(
        request,
        "accounting/statement.html",
        {
            "contact": contact,
            "entries": entries,
            "balance": describe_balance(balance),
            "reversed_ids": reversed_ids,
            "can_manage": can_manage,
            "type_choices": LedgerEntry.Type.choices,
            "filters": {"from": request.GET.get("from", ""), "to": request.GET.get("to", ""), "type": entry_type},
        },
    )


@business_login_required
@require_capability(LEDGER_MANAGE)
@require_http_methods(["GET", "POST"])
def add_entry(request: HttpRequest, contact_id) -> HttpResponse:
    contact = _get_owned_contact(request, contact_id)
    form = LedgerEntryForm(request.POST or None, business=request.business)
    if request.method == "POST" and form.is_valid():
        try:
            post_entry(
                business=request.business,
                contact=contact,
                membership=request.membership,
                entry_type=form.cleaned_data["entry_type"],
                amount=form.cleaned_data["amount"],
                description=form.cleaned_data["description"],
                reference=form.cleaned_data["reference"],
                occurred_on=form.cleaned_data["occurred_on"],
                related_lot=form.cleaned_data["related_lot"],
            )
        except LedgerError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(request, "سند مالی ثبت شد.")
            return redirect("accounting:statement", contact_id=contact.id)
    return render(request, "accounting/entry_form.html", {"form": form, "contact": contact})


@business_login_required
@require_capability(LEDGER_MANAGE)
@require_http_methods(["GET", "POST"])
def reverse_view(request: HttpRequest, entry_id) -> HttpResponse:
    entry = get_object_or_404(
        LedgerEntry.objects.select_related("contact"),
        pk=entry_id,
        business=request.business,
    )
    if request.method == "POST":
        try:
            reverse_entry(entry=entry, membership=request.membership)
        except LedgerError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(request, "سند برگشت خورد.")
        return redirect("accounting:statement", contact_id=entry.contact_id)
    return render(request, "accounting/confirm_reverse.html", {"entry": entry})


def _seller_reservation(request: HttpRequest, reservation_id) -> Reservation:
    """A reservation this business sells on. Any other business — including the
    buyer — gets a 404 rather than a hint that the reservation exists.
    """
    reservation = get_reservation_for_business(request.business, reservation_id)
    if reservation is None or reservation.seller_business_id != request.business.id:
        raise Http404("رزرو یافت نشد.")
    return reservation


def _record_trade_url(reservation: Reservation, contact: Contact | None = None) -> str:
    url = reverse("accounting:record_trade", args=[reservation.id])
    return f"{url}?contact={contact.id}" if contact is not None else url


def _create_contact_for_trade(request: HttpRequest, reservation: Reservation) -> HttpResponse:
    """Create a contact from the confirmation screen and come back with it chosen."""
    quick_form = QuickContactForm(request.POST)
    if not quick_form.is_valid():
        messages.error(request, "نام مخاطب را درست وارد کنید.")
        return redirect(_record_trade_url(reservation))

    link_requested = quick_form.cleaned_data.get("link_to_buyer")
    linked_business = (
        reservation.requester_business
        if link_requested and is_approved_partner(request.business, reservation.requester_business)
        else None
    )
    try:
        contact = create_contact(
            business=request.business,
            membership=request.membership,
            display_name=quick_form.cleaned_data["display_name"],
            phone=quick_form.cleaned_data.get("phone", ""),
            is_customer=True,
            linked_business=linked_business,
        )
    except ContactError as exc:
        messages.error(request, exc.message)
        return redirect(_record_trade_url(reservation))
    except Exception:
        logger.exception("Quick contact creation failed reservation=%s", reservation.id)
        messages.error(request, "ساخت مخاطب با خطا روبه‌رو شد؛ دوباره تلاش کنید.")
        return redirect(_record_trade_url(reservation))

    messages.success(request, "مخاطب ساخته شد و برای این سند انتخاب شد.")
    return redirect(_record_trade_url(reservation, contact))


def _describe_trade_effect(request: HttpRequest, contact: Contact | None, amount) -> dict | None:
    """Balance before/after for the plain-Persian effect statement."""
    if contact is None or not amount:
        return None
    current = current_balance(request.business, contact)
    projected = (current + Decimal(amount)).quantize(Decimal("0.01"))
    return {
        "contact": contact,
        "amount": amount,
        "current": describe_balance(current),
        "projected": describe_balance(projected),
    }


@business_login_required
@require_capability(LEDGER_MANAGE)
@require_http_methods(["GET", "POST"])
def record_trade(request: HttpRequest, reservation_id) -> HttpResponse:
    """Explicit, idempotent step that records a converted sale in the ledger.

    Conversion itself stays non-financial; nothing is posted until this screen is
    submitted with the confirmation ticked. A repeat submit finds the existing
    entry and reports it instead of posting twice.
    """
    reservation = _seller_reservation(request, reservation_id)
    if reservation.status != Reservation.Status.CONVERTED:
        messages.error(request, "فقط برای معامله نهایی‌شده می‌توان سند مالی ثبت کرد.")
        return redirect("reservations:detail", reservation_id=reservation.id)

    existing = trade_entry_for_reservation(request.business, reservation)
    if existing is not None:
        messages.info(request, TRADE_ALREADY_RECORDED)
        return redirect("accounting:statement", contact_id=existing.contact_id)

    if request.method == "POST" and request.POST.get("action") == "create_contact":
        return _create_contact_for_trade(request, reservation)

    action = request.POST.get("action", "") if request.method == "POST" else ""
    chosen_contact = None
    contact_param = request.GET.get("contact", "").strip()
    if contact_param:
        try:
            chosen_contact = get_contact(request.business, contact_param)
        except (Contact.DoesNotExist, ValueError, ValidationError):
            chosen_contact = None
    if chosen_contact is None:
        chosen_contact = suggested_contact_for_reservation(request.business, reservation)

    suggested_amount = suggested_trade_amount(reservation)
    form = TradeEntryForm(
        request.POST or None,
        business=request.business,
        require_confirm=(action == "record"),
        initial={
            "contact": chosen_contact,
            "amount": suggested_amount,
            "occurred_on": timezone.localdate(),
        },
    )

    effect_contact, effect_amount = chosen_contact, suggested_amount
    if request.method == "POST":
        # Validate first so the effect statement mirrors what the seller just typed.
        form.is_valid()
        effect_contact = form.cleaned_data.get("contact")
        effect_amount = form.cleaned_data.get("amount")

    if action == "record" and form.is_valid():
        try:
            entry = post_trade_entry(
                reservation=reservation,
                business=request.business,
                contact=form.cleaned_data["contact"],
                membership=request.membership,
                amount=form.cleaned_data["amount"],
                description=form.cleaned_data.get("description", ""),
                reference=form.cleaned_data.get("reference", ""),
                occurred_on=form.cleaned_data["occurred_on"],
            )
        except LedgerDuplicateError as exc:
            messages.info(request, exc.message)
            contact_id = exc.existing.contact_id if exc.existing else form.cleaned_data["contact"].id
            return redirect("accounting:statement", contact_id=contact_id)
        except LedgerError as exc:
            form.add_error(None, exc.message)
        except Exception:
            logger.exception("Trade ledger entry failed reservation=%s", reservation.id)
            form.add_error(None, "ثبت سند مالی با خطا روبه‌رو شد؛ دوباره تلاش کنید.")
        else:
            messages.success(request, "سند مالی این معامله ثبت شد.")
            return redirect("accounting:statement", contact_id=entry.contact_id)

    return render(
        request,
        "accounting/record_trade.html",
        {
            "reservation": reservation,
            "form": form,
            "quick_form": QuickContactForm(
                initial={"display_name": reservation.requester_business.name}
            ),
            "suggested_amount": suggested_amount,
            "effect": _describe_trade_effect(request, effect_contact, effect_amount),
            "can_link_buyer": is_approved_partner(request.business, reservation.requester_business),
        },
    )


@business_login_required
@require_capability(LEDGER_VIEW)
def print_statement(request: HttpRequest, contact_id) -> HttpResponse:
    contact = _get_owned_contact(request, contact_id)
    entries = contact_statement(request.business, contact)
    balance = current_balance(request.business, contact)
    return render(
        request,
        "accounting/statement_print.html",
        {
            "contact": contact,
            "entries": entries,
            "balance": describe_balance(balance),
            "business": request.business,
        },
    )
