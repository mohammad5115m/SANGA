from __future__ import annotations

import logging

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from apps.businesses.decorators import business_login_required, require_capability
from apps.businesses.permissions import INVOICE_MANAGE, INVOICE_VIEW
from apps.core.pagination import ROW_PAGE_SIZE, paginate

from .forms import InvoiceLineFormSet, ManualInvoiceForm
from .models import SalesInvoice
from .selectors import filter_invoices, get_invoice, invoices_for
from .services import InvoiceError, cancel_invoice, create_manual_invoice, issue_invoice

logger = logging.getLogger(__name__)


@business_login_required
@require_capability(INVOICE_VIEW)
def invoice_list(request: HttpRequest) -> HttpResponse:
    status = request.GET.get("status", "")
    q = request.GET.get("q", "").strip()
    qs = filter_invoices(invoices_for(request.business), status=status, q=q)
    page = paginate(request, qs, per_page=ROW_PAGE_SIZE)
    return render(
        request,
        "invoicing/list.html",
        {
            "invoices": page.object_list,
            "page": page,
            "status": status,
            "q": q,
            "status_choices": SalesInvoice.Status.choices,
            "can_manage": request.membership.has_capability(INVOICE_MANAGE),
        },
    )


@business_login_required
@require_capability(INVOICE_VIEW)
def invoice_detail(request: HttpRequest, invoice_id) -> HttpResponse:
    invoice = get_invoice(request.business, invoice_id)
    if invoice is None:
        messages.error(request, "فاکتور یافت نشد.")
        return redirect("invoicing:list")
    return render(
        request,
        "invoicing/detail.html",
        {
            "invoice": invoice,
            "is_seller": invoice.seller_business_id == request.business.id,
            "can_manage": request.membership.has_capability(INVOICE_MANAGE),
        },
    )


@business_login_required
@require_capability(INVOICE_VIEW)
def invoice_print(request: HttpRequest, invoice_id) -> HttpResponse:
    """Print-friendly view. Browser printing is the whole delivery mechanism —
    a PDF pipeline would be a subsystem to maintain for the same output."""
    invoice = get_invoice(request.business, invoice_id)
    if invoice is None:
        messages.error(request, "فاکتور یافت نشد.")
        return redirect("invoicing:list")
    return render(request, "invoicing/print.html", {"invoice": invoice})


@business_login_required
@require_capability(INVOICE_MANAGE)
@require_http_methods(["GET", "POST"])
def invoice_create(request: HttpRequest) -> HttpResponse:
    form = ManualInvoiceForm(request.POST or None, business=request.business)
    formset = InvoiceLineFormSet(request.POST or None, prefix="lines")

    if request.method == "POST" and form.is_valid() and formset.is_valid():
        lines = [
            {
                "product_name": line.get("product_name"),
                "stone_type": line.get("stone_type", ""),
                "grade": line.get("grade", ""),
                "quantity": line.get("quantity"),
                "unit_price": line.get("unit_price"),
                "item": None,
            }
            for line in formset.cleaned_data
            if line and not line.get("DELETE") and line.get("product_name")
        ]
        try:
            invoice = create_manual_invoice(
                business=request.business,
                membership=request.membership,
                lines=lines,
                customer_name=form.cleaned_data.get("customer_name", ""),
                customer_phone=form.cleaned_data.get("customer_phone", ""),
                notes=form.cleaned_data.get("notes", ""),
                issue_date=form.cleaned_data.get("issue_date"),
            )
        except InvoiceError as exc:
            form.add_error(None, exc.message)
        else:
            messages.success(request, f"فاکتور {invoice.number} صادر شد.")
            return redirect("invoicing:detail", invoice_id=invoice.id)

    return render(request, "invoicing/form.html", {"form": form, "formset": formset})


@business_login_required
@require_capability(INVOICE_MANAGE)
@require_POST
def invoice_issue(request: HttpRequest, invoice_id) -> HttpResponse:
    invoice = get_invoice(request.business, invoice_id)
    if invoice is None or invoice.seller_business_id != request.business.id:
        messages.error(request, "فاکتور یافت نشد.")
        return redirect("invoicing:list")
    try:
        issue_invoice(invoice=invoice, membership=request.membership)
    except InvoiceError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "فاکتور صادر شد.")
    return redirect("invoicing:detail", invoice_id=invoice.id)


@business_login_required
@require_capability(INVOICE_MANAGE)
@require_POST
def invoice_cancel(request: HttpRequest, invoice_id) -> HttpResponse:
    invoice = get_invoice(request.business, invoice_id)
    if invoice is None or invoice.seller_business_id != request.business.id:
        messages.error(request, "فاکتور یافت نشد.")
        return redirect("invoicing:list")
    try:
        cancel_invoice(invoice=invoice, membership=request.membership)
    except InvoiceError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "فاکتور باطل شد. این کار مانده حساب را تغییر نمی‌دهد.")
    return redirect("invoicing:detail", invoice_id=invoice.id)
