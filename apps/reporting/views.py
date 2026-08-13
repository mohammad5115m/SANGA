from __future__ import annotations

import logging

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils.dateparse import parse_date

from apps.accounting.reports import business_aging
from apps.businesses.decorators import business_login_required, require_capability
from apps.businesses.permissions import LEDGER_VIEW

from . import reports
from .reports import DateRange

logger = logging.getLogger(__name__)

#: Every report, in the order they appear in the switcher. Declared once so the
#: nav, the page titles and the routing cannot drift apart.
REPORTS: tuple[tuple[str, str], ...] = (
    ("summary", "خلاصه فروش"),
    ("by_colleague", "فروش به تفکیک همکار"),
    ("by_stone_type", "فروش به تفکیک نوع سنگ"),
    ("by_product", "فروش به تفکیک محصول"),
    ("debtors", "بدهکاران"),
    ("creditors", "بستانکاران"),
    ("invoices", "فاکتورها"),
    ("aging", "گزارش سنی بدهی"),
    ("stock_check", "نیازمند تأیید موجودی"),
    ("price_check", "نیازمند بررسی قیمت"),
)

REPORT_KEYS = {key for key, _ in REPORTS}


def _window(request: HttpRequest) -> DateRange:
    raw_from = (request.GET.get("from") or "").strip()
    raw_to = (request.GET.get("to") or "").strip()
    if not raw_from and not raw_to:
        # Unbounded by default: "everything" is a safer first answer than a
        # window the reader did not choose and might not notice.
        return DateRange()
    try:
        return DateRange(date_from=parse_date(raw_from) if raw_from else None,
                         date_to=parse_date(raw_to) if raw_to else None)
    except ValueError:
        return DateRange()


@business_login_required
@require_capability(LEDGER_VIEW)
def report_view(request: HttpRequest, key: str = "summary") -> HttpResponse:
    """One view, one template per report body.

    Ten near-identical views would be ten places to forget the tenant scope.
    """
    if key not in REPORT_KEYS:
        key = "summary"
    business = request.business
    window = _window(request)

    context = {
        "reports": REPORTS,
        "active": key,
        "title": dict(REPORTS)[key],
        "window": window,
        "filters": {"from": request.GET.get("from", ""), "to": request.GET.get("to", "")},
        "summary": reports.sales_summary(business, window),
        "money": reports.money_movement(business, window),
    }

    if key == "by_colleague":
        context["rows"] = reports.sales_by_colleague(business, window)
    elif key == "by_stone_type":
        context["rows"] = reports.sales_by_stone_type(business, window)
    elif key == "by_product":
        context["rows"] = reports.sales_by_product(business, window)
    elif key == "debtors":
        context["balances"] = reports.balances(business, state="debtor")
    elif key == "creditors":
        context["balances"] = reports.balances(business, state="creditor")
    elif key == "invoices":
        context["invoices"] = reports.invoices_in_range(business, window)[:200]
        context["invoice_summary"] = reports.invoice_summary(business, window)
    elif key == "aging":
        context["aging"] = business_aging(business)
    elif key == "stock_check":
        context["items"] = reports.stock_needing_confirmation(business)[:200]
    elif key == "price_check":
        context["prices"] = reports.prices_needing_confirmation(business)[:200]

    context["print_mode"] = request.GET.get("print") == "1"
    template = "reporting/print.html" if context["print_mode"] else "reporting/report.html"
    return render(request, template, context)
