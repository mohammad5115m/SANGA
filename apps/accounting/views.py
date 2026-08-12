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
from django.utils.http import urlencode
from django.views.decorators.http import require_http_methods

from apps.businesses.decorators import business_login_required, require_capability
from apps.businesses.permissions import LEDGER_MANAGE, LEDGER_VIEW
from apps.contacts.models import Contact
from apps.contacts.selectors import get_contact
from apps.contacts.services import ContactError, create_contact
from apps.purchase_requests.models import PurchaseOffer

from .forms import LedgerEntryForm, QuickContactForm, TradeEntryForm
from .models import LedgerEntry
from .reports import business_aging, contact_aging
from .selectors import (
    BALANCE_SORTS,
    BALANCE_STATE_LABELS,
    accepted_offer_for,
    business_financial_summary,
    contact_balances,
    contact_statement,
    current_balance,
    describe_balance,
    offer_counterparty,
    reversed_entry_ids,
    statement_totals,
    suggested_amount_for_offer,
    suggested_contact_for_offer,
    trade_entry_for_offer,
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

# Filter chips on the ledger index, in display order.
STATE_OPTIONS: list[tuple[str, str]] = [("", "همه"), *BALANCE_STATE_LABELS.items()]

# Sort choices offered on the ledger index; keys must exist in ``BALANCE_SORTS``.
SORT_OPTIONS: list[tuple[str, str]] = [
    ("name", "نام مخاطب"),
    ("debtor", "بیشترین بدهکاری"),
    ("creditor", "بیشترین بستانکاری"),
]


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
    """Entry point for the ledger: the business-wide summary plus one labeled row
    per contact, filterable by accounting state and sortable by balance.
    """
    state = request.GET.get("state", "").strip()
    if state not in BALANCE_STATE_LABELS:
        state = ""
    sort = request.GET.get("sort", "").strip()
    if sort not in BALANCE_SORTS:
        sort = "name"

    rows = [
        {"contact": contact, "balance": describe_balance(contact.balance)}
        for contact in contact_balances(request.business, state=state, sort=sort)
    ]
    return render(
        request,
        "accounting/index.html",
        {
            "rows": rows,
            "summary": business_financial_summary(request.business),
            "state": state,
            "sort": sort,
            "state_options": STATE_OPTIONS,
            "sort_options": SORT_OPTIONS,
            "can_manage": request.membership.has_capability(LEDGER_MANAGE),
        },
    )


@business_login_required
@require_capability(LEDGER_VIEW)
def aging_report(request: HttpRequest) -> HttpResponse:
    """گزارش سنی بدهی for the whole business, oldest debts first per contact."""
    return render(
        request,
        "accounting/aging.html",
        {
            "report": business_aging(request.business),
            "summary": business_financial_summary(request.business),
        },
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
            # Totals follow the active filters; the aging report deliberately does
            # not — how old a debt is cannot depend on what the viewer filtered.
            "totals": statement_totals(entries),
            "aging": contact_aging(request.business, contact),
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


def _offer_for_trade(request: HttpRequest) -> PurchaseOffer | None:
    """The accepted offer this screen was started from, if any.

    A business that is party to neither side of the offer gets a 404 rather than
    a hint that the offer exists. Recording a trade without an offer is the
    normal case — most trades here are agreed offline.
    """
    offer_id = (request.POST.get("offer") or request.GET.get("offer") or "").strip()
    if not offer_id:
        return None
    try:
        offer = accepted_offer_for(request.business, offer_id)
    except (ValueError, ValidationError):
        offer = None
    if offer is None:
        raise Http404("پیشنهاد یافت نشد.")
    return offer


def _record_trade_url(offer: PurchaseOffer | None, contact: Contact | None = None) -> str:
    params = {}
    if offer is not None:
        params["offer"] = str(offer.id)
    if contact is not None:
        params["contact"] = str(contact.id)
    url = reverse("accounting:record_trade")
    return f"{url}?{urlencode(params)}" if params else url


def _default_entry_type(request: HttpRequest, offer: PurchaseOffer | None) -> str:
    """A sale by default; a purchase when this business accepted someone's offer."""
    if offer is not None and offer.purchase_request.business_id == request.business.id:
        return LedgerEntry.Type.PURCHASE.value
    return LedgerEntry.Type.SALE.value


def _create_contact_for_trade(
    request: HttpRequest, offer: PurchaseOffer | None
) -> HttpResponse:
    """Create a contact from the trade screen and come back with it chosen."""
    quick_form = QuickContactForm(request.POST)
    if not quick_form.is_valid():
        messages.error(request, "نام مخاطب را درست وارد کنید.")
        return redirect(_record_trade_url(offer))

    counterparty = offer_counterparty(request.business, offer) if offer else None
    link_requested = quick_form.cleaned_data.get("link_to_counterparty")
    linked_business = counterparty if (link_requested and counterparty) else None
    try:
        contact = create_contact(
            business=request.business,
            membership=request.membership,
            display_name=quick_form.cleaned_data["display_name"],
            phone=quick_form.cleaned_data.get("phone", ""),
            linked_business=linked_business,
        )
    except ContactError as exc:
        messages.error(request, exc.message)
        return redirect(_record_trade_url(offer))
    except Exception:
        logger.exception("Quick contact creation failed business=%s", request.business.id)
        messages.error(request, "ساخت مخاطب با خطا روبه‌رو شد؛ دوباره تلاش کنید.")
        return redirect(_record_trade_url(offer))

    messages.success(request, "مخاطب ساخته شد و برای این سند انتخاب شد.")
    return redirect(_record_trade_url(offer, contact))


def _describe_trade_effect(
    request: HttpRequest, contact: Contact | None, entry_type: str, amount
) -> dict | None:
    """Balance before/after for the plain-Persian effect statement."""
    if contact is None or not amount:
        return None
    direction = -1 if entry_type == LedgerEntry.Type.PURCHASE.value else 1
    current = current_balance(request.business, contact)
    projected = (current + Decimal(amount) * direction).quantize(Decimal("0.01"))
    return {
        "contact": contact,
        "amount": amount,
        "current": describe_balance(current),
        "projected": describe_balance(projected),
    }


@business_login_required
@require_capability(LEDGER_MANAGE)
@require_http_methods(["GET", "POST"])
def record_trade(request: HttpRequest) -> HttpResponse:
    """Record a trade in the ledger: manually, or started from an accepted offer.

    Nothing is posted until the form is submitted with the confirmation ticked.
    When an offer is involved, a repeat submit finds the existing entry and
    reports it instead of posting twice.
    """
    offer = _offer_for_trade(request)
    if offer is not None:
        existing = trade_entry_for_offer(request.business, offer)
        if existing is not None:
            messages.info(request, TRADE_ALREADY_RECORDED)
            return redirect("accounting:statement", contact_id=existing.contact_id)

    if request.method == "POST" and request.POST.get("action") == "create_contact":
        return _create_contact_for_trade(request, offer)

    action = request.POST.get("action", "") if request.method == "POST" else ""
    chosen_contact = None
    contact_param = request.GET.get("contact", "").strip()
    if contact_param:
        try:
            chosen_contact = get_contact(request.business, contact_param)
        except (Contact.DoesNotExist, ValueError, ValidationError):
            chosen_contact = None
    if chosen_contact is None and offer is not None:
        chosen_contact = suggested_contact_for_offer(request.business, offer)

    suggested_amount = suggested_amount_for_offer(offer) if offer is not None else None
    default_type = _default_entry_type(request, offer)
    form = TradeEntryForm(
        request.POST or None,
        business=request.business,
        require_confirm=(action == "record"),
        initial={
            "entry_type": default_type,
            "contact": chosen_contact,
            "amount": suggested_amount,
            "occurred_on": timezone.localdate(),
            "related_lot": offer.lot if offer is not None else None,
        },
    )

    effect_contact, effect_amount, effect_type = chosen_contact, suggested_amount, default_type
    if request.method == "POST":
        # Validate first so the effect statement mirrors what the user just typed.
        form.is_valid()
        effect_contact = form.cleaned_data.get("contact")
        effect_amount = form.cleaned_data.get("amount")
        effect_type = form.cleaned_data.get("entry_type") or default_type

    if action == "record" and form.is_valid():
        try:
            entry = post_trade_entry(
                business=request.business,
                contact=form.cleaned_data["contact"],
                membership=request.membership,
                entry_type=form.cleaned_data["entry_type"],
                amount=form.cleaned_data["amount"],
                description=form.cleaned_data.get("description", ""),
                reference=form.cleaned_data.get("reference", ""),
                occurred_on=form.cleaned_data["occurred_on"],
                related_lot=form.cleaned_data.get("related_lot"),
                related_offer=offer,
            )
        except LedgerDuplicateError as exc:
            messages.info(request, exc.message)
            contact_id = exc.existing.contact_id if exc.existing else form.cleaned_data["contact"].id
            return redirect("accounting:statement", contact_id=contact_id)
        except LedgerError as exc:
            form.add_error(None, exc.message)
        except Exception:
            logger.exception("Trade ledger entry failed business=%s", request.business.id)
            form.add_error(None, "ثبت سند مالی با خطا روبه‌رو شد؛ دوباره تلاش کنید.")
        else:
            messages.success(request, "سند مالی این معامله ثبت شد.")
            return redirect("accounting:statement", contact_id=entry.contact_id)

    counterparty = offer_counterparty(request.business, offer) if offer else None
    return render(
        request,
        "accounting/record_trade.html",
        {
            "offer": offer,
            "counterparty": counterparty,
            "form": form,
            "quick_form": QuickContactForm(
                initial={"display_name": counterparty.name if counterparty else ""}
            ),
            "suggested_amount": suggested_amount,
            "effect": _describe_trade_effect(request, effect_contact, effect_type, effect_amount),
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
            "totals": statement_totals(entries),
            "balance": describe_balance(balance),
            "business": request.business,
        },
    )
