from __future__ import annotations

import logging

from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_http_methods

from apps.businesses.decorators import business_login_required, require_capability
from apps.businesses.directory import get_colleague
from apps.businesses.permissions import LEDGER_MANAGE, LEDGER_VIEW

from .forms import ManualEntryForm
from .models import LedgerEntry
from .reports import business_aging, counterparty_aging
from .selectors import (
    BALANCE_SORTS,
    BALANCE_STATE_LABELS,
    business_financial_summary,
    counterparty_balances,
    counterparty_statement,
    current_balance,
    describe_balance,
    legacy_entries,
    reversed_entry_ids,
    statement_totals,
)
from .services import LedgerError, post_manual_entry, reverse_entry

logger = logging.getLogger(__name__)

# Filter chips on the ledger index, in display order.
STATE_OPTIONS: list[tuple[str, str]] = [("", "همه"), *BALANCE_STATE_LABELS.items()]

SORT_OPTIONS: list[tuple[str, str]] = [
    ("name", "نام همکار"),
    ("debtor", "بیشترین بدهکاری"),
    ("creditor", "بیشترین بستانکاری"),
]


def _colleague_or_404(request: HttpRequest, business_id):
    colleague = get_colleague(request.business, business_id)
    if colleague is None:
        raise Http404("همکار یافت نشد.")
    return colleague


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
    """The business-wide summary plus one labeled row per colleague account."""
    state = request.GET.get("state", "").strip()
    if state not in BALANCE_STATE_LABELS:
        state = ""
    sort = request.GET.get("sort", "").strip()
    if sort not in BALANCE_SORTS:
        sort = "name"

    rows = [
        {"colleague": colleague, "balance": describe_balance(colleague.balance)}
        for colleague in counterparty_balances(request.business, state=state, sort=sort)
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
            "legacy_count": legacy_entries(request.business).count(),
        },
    )


@business_login_required
@require_capability(LEDGER_VIEW)
def aging_report(request: HttpRequest) -> HttpResponse:
    """گزارش سنی بدهی for the whole business, oldest debts first per colleague."""
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
def statement(request: HttpRequest, business_id) -> HttpResponse:
    colleague = _colleague_or_404(request, business_id)
    date_from = _parse_date(request.GET.get("from", ""))
    date_to = _parse_date(request.GET.get("to", ""))
    entry_type = request.GET.get("type", "").strip()

    entries = counterparty_statement(
        request.business,
        colleague,
        date_from=date_from,
        date_to=date_to,
        entry_type=entry_type,
    )
    balance = current_balance(request.business, colleague)

    from apps.invoicing.selectors import invoices_between

    return render(
        request,
        "accounting/statement.html",
        {
            "colleague": colleague,
            "entries": entries,
            # Totals follow the active filters; the aging report deliberately
            # does not — how old a debt is cannot depend on what the viewer
            # filtered.
            "totals": statement_totals(entries),
            "aging": counterparty_aging(request.business, colleague),
            "balance": describe_balance(balance),
            "reversed_ids": reversed_entry_ids(request.business, colleague),
            "can_manage": request.membership.has_capability(LEDGER_MANAGE),
            "type_choices": LedgerEntry.Type.choices,
            "invoices": invoices_between(request.business, colleague)[:20],
            "filters": {
                "from": request.GET.get("from", ""),
                "to": request.GET.get("to", ""),
                "type": entry_type,
            },
        },
    )


@business_login_required
@require_capability(LEDGER_MANAGE)
@require_http_methods(["GET", "POST"])
def add_entry(request: HttpRequest, business_id) -> HttpResponse:
    """دریافت / پرداخت / اصلاح — the only four manual entries there are.

    A sale can never be posted here: it reaches the books through finalizing a
    Trade, which is the one authoritative event.
    """
    colleague = _colleague_or_404(request, business_id)
    form = ManualEntryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            post_manual_entry(
                business=request.business,
                counterparty=colleague,
                membership=request.membership,
                entry_type=form.cleaned_data["entry_type"],
                amount=form.cleaned_data["amount"],
                description=form.cleaned_data["description"],
                reference=form.cleaned_data["reference"],
                occurred_on=form.cleaned_data["occurred_on"],
            )
        except LedgerError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(request, "سند مالی ثبت شد.")
            return redirect("accounting:statement", business_id=colleague.id)
    return render(request, "accounting/entry_form.html", {"form": form, "colleague": colleague})


@business_login_required
@require_capability(LEDGER_MANAGE)
@require_http_methods(["GET", "POST"])
def reverse_view(request: HttpRequest, entry_id) -> HttpResponse:
    entry = get_object_or_404(
        LedgerEntry.objects.select_related("counterparty_business"),
        pk=entry_id,
        business=request.business,
    )
    if request.method == "POST":
        try:
            reverse_entry(entry=entry, membership=request.membership)
        except LedgerError as exc:
            messages.error(request, exc.message)
            return redirect("accounting:index")
        messages.success(request, "سند برگشت خورد.")
        return redirect("accounting:statement", business_id=entry.counterparty_business_id)
    return render(request, "accounting/confirm_reverse.html", {"entry": entry})


@business_login_required
@require_capability(LEDGER_VIEW)
def print_statement(request: HttpRequest, business_id) -> HttpResponse:
    colleague = _colleague_or_404(request, business_id)
    entries = counterparty_statement(request.business, colleague)
    return render(
        request,
        "accounting/statement_print.html",
        {
            "colleague": colleague,
            "entries": entries,
            "totals": statement_totals(entries),
            "balance": describe_balance(current_balance(request.business, colleague)),
            "business": request.business,
        },
    )


@business_login_required
@require_capability(LEDGER_VIEW)
def legacy_list(request: HttpRequest) -> HttpResponse:
    """Pre-V2 entries that could not be mapped to a colleague Business.

    Shown rather than hidden: this is real money, and a business that cannot see
    it will assume the migration lost it.
    """
    return render(
        request,
        "accounting/legacy.html",
        {"entries": legacy_entries(request.business)},
    )
