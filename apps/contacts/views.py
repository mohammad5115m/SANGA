from __future__ import annotations

import logging

from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from apps.businesses.decorators import business_login_required, require_capability
from apps.businesses.permissions import CUSTOMERS_MANAGE

from .forms import ContactForm
from .models import Contact
from .selectors import KIND_FILTERS, contacts_for_business, get_contact
from .services import (
    ContactError,
    archive_contact,
    create_contact,
    update_contact,
)

logger = logging.getLogger(__name__)


def _get_owned_contact(request: HttpRequest, contact_id) -> Contact:
    try:
        return get_contact(request.business, contact_id)
    except Contact.DoesNotExist as exc:
        raise Http404("مخاطب یافت نشد.") from exc


@business_login_required
@require_capability(CUSTOMERS_MANAGE)
def contact_list(request: HttpRequest) -> HttpResponse:
    q = request.GET.get("q", "").strip()
    kind = request.GET.get("kind", "").strip()
    if kind not in KIND_FILTERS:
        kind = ""
    contacts = contacts_for_business(request.business, q=q, kind=kind)
    return render(
        request,
        "contacts/list.html",
        {
            "contacts": contacts,
            "q": q,
            "kind": kind,
        },
    )


@business_login_required
@require_capability(CUSTOMERS_MANAGE)
def contact_detail(request: HttpRequest, contact_id) -> HttpResponse:
    contact = _get_owned_contact(request, contact_id)

    # Financial summary is only exposed to members with the ledger.view
    # capability; contact management alone must not reveal balances.
    from apps.businesses.permissions import LEDGER_VIEW, PRICES_VIEW
    from apps.accounting.selectors import current_balance, describe_balance
    from apps.pricing.selectors import contact_prices_for_contact

    balance = None
    if request.membership.has_capability(LEDGER_VIEW):
        balance = describe_balance(current_balance(request.business, contact))

    # Read-only here: overrides are created and removed on the lot's price screen.
    contact_prices = None
    if request.membership.has_capability(PRICES_VIEW):
        contact_prices = contact_prices_for_contact(request.business, contact)

    return render(
        request,
        "contacts/detail.html",
        {"contact": contact, "balance": balance, "contact_prices": contact_prices},
    )


@business_login_required
@require_capability(CUSTOMERS_MANAGE)
@require_http_methods(["GET", "POST"])
def contact_create(request: HttpRequest) -> HttpResponse:
    form = ContactForm(request.POST or None, business=request.business)
    if request.method == "POST" and form.is_valid():
        try:
            contact = create_contact(
                business=request.business,
                membership=request.membership,
                display_name=form.cleaned_data["display_name"],
                phone=form.cleaned_data["phone"],
                address=form.cleaned_data["address"],
                notes=form.cleaned_data["notes"],
                is_customer=form.cleaned_data["is_customer"],
                is_supplier=form.cleaned_data["is_supplier"],
                is_trader=form.cleaned_data["is_trader"],
                linked_business=form.cleaned_data["linked_business"],
            )
        except ContactError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(request, "مخاطب ثبت شد.")
            return redirect("contacts:detail", contact_id=contact.id)
    return render(
        request,
        "contacts/form.html",
        {"form": form, "mode": "create"},
    )


@business_login_required
@require_capability(CUSTOMERS_MANAGE)
@require_http_methods(["GET", "POST"])
def contact_edit(request: HttpRequest, contact_id) -> HttpResponse:
    contact = _get_owned_contact(request, contact_id)
    initial = {
        "display_name": contact.display_name,
        "phone": contact.phone,
        "address": contact.address,
        "notes": contact.notes,
        "is_customer": contact.is_customer,
        "is_supplier": contact.is_supplier,
        "is_trader": contact.is_trader,
        "linked_business": contact.linked_business_id,
    }
    form = ContactForm(request.POST or None, business=request.business, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            update_contact(
                contact=contact,
                membership=request.membership,
                display_name=form.cleaned_data["display_name"],
                phone=form.cleaned_data["phone"],
                address=form.cleaned_data["address"],
                notes=form.cleaned_data["notes"],
                is_customer=form.cleaned_data["is_customer"],
                is_supplier=form.cleaned_data["is_supplier"],
                is_trader=form.cleaned_data["is_trader"],
                linked_business=form.cleaned_data["linked_business"],
            )
        except ContactError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(request, "مخاطب به‌روزرسانی شد.")
            return redirect("contacts:detail", contact_id=contact.id)
    return render(
        request,
        "contacts/form.html",
        {"form": form, "mode": "edit", "contact": contact},
    )


@business_login_required
@require_capability(CUSTOMERS_MANAGE)
@require_http_methods(["GET", "POST"])
def contact_archive(request: HttpRequest, contact_id) -> HttpResponse:
    contact = _get_owned_contact(request, contact_id)
    if request.method == "POST":
        try:
            archive_contact(contact=contact, membership=request.membership)
        except ContactError as exc:
            messages.error(request, exc.message)
            return redirect("contacts:detail", contact_id=contact.id)
        messages.success(request, "مخاطب بایگانی شد.")
        return redirect("contacts:list")
    return render(request, "contacts/confirm_archive.html", {"contact": contact})
