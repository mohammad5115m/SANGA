from __future__ import annotations

import logging

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import FileResponse, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.utils.dateparse import parse_date
from django.utils.http import content_disposition_header
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_http_methods, require_POST

from apps.businesses.decorators import (
    business_login_required,
    require_business_entitlement,
    require_capability,
)
from apps.businesses.entitlements import ISSUE_INVOICES, has_entitlement
from apps.businesses.permissions import INVOICE_MANAGE, INVOICE_VIEW
from apps.core.pagination import ROW_PAGE_SIZE, paginate

from .calculations import DISCOUNT_AMOUNT, to_display_amount
from .documents import build_invoice_document, build_preview_document
from .forms import (
    BusinessInvoiceSettingsForm,
    InvoiceCancelForm,
    InvoiceLineFormSet,
    InvoiceTemplateNameForm,
    ManualInvoiceForm,
    invoice_initial,
    invoice_line_initials,
    new_submission_id,
)
from .models import InvoiceTemplate, SalesInvoice
from .rendering import (
    DocumentRenderError,
    document_html,
    export_allowed,
    render_pdf,
    render_png,
)
from .selectors import filter_invoices, get_invoice, invoices_for, recent_customers
from .services import (
    InvoiceError,
    _calculate,
    _clean_lines,
    cancel_invoice,
    create_manual_invoice,
    create_replacement_invoice,
    delete_draft_invoice,
    duplicate_invoice,
    issue_invoice,
    save_as_template,
    update_draft_invoice,
)

logger = logging.getLogger(__name__)


def _can_manage_invoices(request: HttpRequest) -> bool:
    return request.membership.has_capability(INVOICE_MANAGE) and has_entitlement(
        request.business, ISSUE_INVOICES
    )


def _initial_step(form: ManualInvoiceForm, formset) -> int:
    if not form.is_bound:
        return 1
    if formset.errors or formset.non_form_errors() or any(
        form.errors.get(name)
        for name in (
            "invoice_discount_type",
            "invoice_discount_value",
            "tax_amount",
            "shipping_amount",
            "adjustment_amount",
            "paid_amount",
        )
    ):
        return 2
    if any(
        form.errors.get(name)
        for name in (
            "palette",
            "primary_color",
            "header_style",
            "logo_size",
            "buyer_signature",
        )
    ):
        return 3
    return 1


def _invoice_or_redirect(request, invoice_id):
    invoice = get_invoice(request.business, invoice_id)
    if invoice is None:
        messages.error(request, "فاکتور یافت نشد.")
    return invoice


def _submitted_lines(formset) -> list[dict]:
    result = []
    for index, line in enumerate(formset.cleaned_data):
        if not line or line.get("DELETE"):
            continue
        if not (line.get("item") or line.get("product_name")):
            continue
        result.append(
            {
                "product_name": line.get("product_name") or "",
                "stone_type": line.get("stone_type") or "",
                "grade": line.get("grade") or "",
                "description": line.get("description") or "",
                "quantity": line.get("quantity"),
                "unit": line.get("unit"),
                "unit_price": line.get("unit_price"),
                "discount_type": line.get("discount_type"),
                "discount_value": line.get("discount_value") or 0,
                "item": line.get("item"),
                "sort_order": line.get("ORDER") if line.get("ORDER") is not None else index,
            }
        )
    return result


def _header_data(form: ManualInvoiceForm) -> dict:
    fields = (
        "customer_name",
        "customer_phone",
        "buyer_address",
        "issue_date",
        "currency",
        "display_unit",
        "invoice_discount_type",
        "invoice_discount_value",
        "tax_amount",
        "shipping_amount",
        "adjustment_amount",
        "paid_amount",
        "notes",
        "payment_terms",
        "buyer_signature",
        "remove_buyer_signature",
    )
    return {field: form.cleaned_data.get(field) for field in fields} | {
        "appearance": form.appearance()
    }


@business_login_required
@require_capability(INVOICE_VIEW)
def invoice_list(request: HttpRequest) -> HttpResponse:
    status = request.GET.get("status", "")
    q = request.GET.get("q", "").strip()
    direction = request.GET.get("direction", "")
    origin = request.GET.get("origin", "")
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")
    page = paginate(
        request,
        filter_invoices(
            invoices_for(request.business),
            business=request.business,
            status=status,
            q=q,
            direction=direction,
            origin=origin,
            date_from=parse_date(date_from),
            date_to=parse_date(date_to),
        ),
        per_page=ROW_PAGE_SIZE,
    )
    return render(
        request,
        "invoicing/list.html",
        {
            "invoices": page.object_list,
            "page": page,
            "status": status,
            "q": q,
            "direction": direction,
            "origin": origin,
            "date_from": date_from,
            "date_to": date_to,
            "status_choices": SalesInvoice.Status.choices,
            "can_manage": _can_manage_invoices(request),
        },
    )


@business_login_required
@require_capability(INVOICE_VIEW)
def invoice_detail(request: HttpRequest, invoice_id) -> HttpResponse:
    invoice = _invoice_or_redirect(request, invoice_id)
    if invoice is None:
        return redirect("invoicing:list")
    return render(
        request,
        "invoicing/detail.html",
        {
            "invoice": invoice,
            "document": build_invoice_document(invoice),
            "is_seller": invoice.seller_business_id == request.business.id,
            "can_manage": _can_manage_invoices(request),
            "cancel_form": InvoiceCancelForm(),
            "template_form": InvoiceTemplateNameForm(
                initial={"name": f"قالب {invoice.buyer_name}"}
            ),
        },
    )


@business_login_required
@require_capability(INVOICE_VIEW)
@xframe_options_sameorigin
def invoice_print(request: HttpRequest, invoice_id) -> HttpResponse:
    invoice = _invoice_or_redirect(request, invoice_id)
    if invoice is None:
        return redirect("invoicing:list")
    mode = "embedded" if request.GET.get("embedded") == "1" else "print"
    response = HttpResponse(document_html(invoice, mode=mode))
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; img-src data:; script-src 'self'; "
        "frame-ancestors 'self'; base-uri 'none'; form-action 'none'"
    )
    return response


def _template_initial(template: InvoiceTemplate) -> tuple[dict, list[dict]]:
    payload = template.payload or {}
    if template.schema_version != 1 or payload.get("schema_version", 1) != 1:
        raise ValueError("Unsupported invoice template schema")
    currency = payload.get("currency", "IRR")
    display_unit = payload.get("display_unit", currency)

    def display(value):
        return to_display_amount(value or 0, currency=currency, display_unit=display_unit)

    discount_value = payload.get("invoice_discount_value", 0)
    if payload.get("invoice_discount_type") == DISCOUNT_AMOUNT:
        discount_value = display(discount_value)
    appearance = payload.get("appearance") or {}
    header = {
        **{key: payload.get(key, "") for key in (
            "customer_name", "customer_phone", "buyer_address", "notes", "payment_terms"
        )},
        "currency": currency,
        "display_unit": display_unit,
        "invoice_discount_type": payload.get("invoice_discount_type", "none"),
        "invoice_discount_value": discount_value,
        "tax_amount": display(payload.get("tax_amount", 0)),
        "shipping_amount": display(payload.get("shipping_amount", 0)),
        "adjustment_amount": display(payload.get("adjustment_amount", 0)),
        "paid_amount": 0,
        "palette": appearance.get("palette", "forest"),
        "primary_color": appearance.get("primary_color", "#1f513c"),
        "header_style": appearance.get("header_style", "modern"),
        "logo_size": appearance.get("logo_size", "medium"),
        "show_bank_information": appearance.get("show_bank_information", True),
        "show_stamp": appearance.get("show_stamp", True),
        "show_signature": appearance.get("show_signature", True),
    }
    lines = []
    from apps.inventory.models import InventoryLot

    requested_item_ids = [
        source.get("item_id") for source in payload.get("lines", []) if source.get("item_id")
    ]
    usable_item_ids = {
        str(item_id)
        for item_id in InventoryLot.objects.filter(
            business=template.business, id__in=requested_item_ids
        ).values_list("id", flat=True)
    }
    for source in payload.get("lines", []):
        discount = source.get("discount_value", 0)
        if source.get("discount_type") == DISCOUNT_AMOUNT:
            discount = display(discount)
        lines.append(
            {
                **source,
                # A deleted/unavailable lot becomes a free-text historical line
                # instead of a hidden invalid identifier that blocks saving.
                "item": (
                    source.get("item_id")
                    if str(source.get("item_id")) in usable_item_ids
                    else None
                ),
                "unit_price": display(source.get("unit_price", 0)),
                "discount_value": discount,
                "ORDER": source.get("sort_order", len(lines)),
            }
        )
    return header, lines


@business_login_required
@require_capability(INVOICE_MANAGE)
@require_business_entitlement(ISSUE_INVOICES)
@require_http_methods(["GET", "POST"])
def invoice_create(request: HttpRequest) -> HttpResponse:
    initial = None
    line_initial = None
    template_id = request.GET.get("template")
    if template_id:
        try:
            template = InvoiceTemplate.objects.filter(
                business=request.business, pk=template_id
            ).first()
        except (ValidationError, TypeError, ValueError):
            template = None
        if template:
            try:
                initial, line_initial = _template_initial(template)
            except (TypeError, ValueError):
                messages.warning(request, "داده‌های این قالب معتبر نیست؛ فرم خالی باز شد.")
    form_initial = {**(initial or {})}
    if request.method == "GET":
        form_initial["submission_id"] = new_submission_id()
    form = ManualInvoiceForm(
        request.POST or None,
        request.FILES or None,
        business=request.business,
        initial=form_initial,
    )
    formset = InvoiceLineFormSet(
        request.POST or None,
        prefix="lines",
        business=request.business,
        initial=line_initial,
    )
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        action = request.POST.get("action", "draft")
        try:
            invoice = create_manual_invoice(
                business=request.business,
                membership=request.membership,
                lines=_submitted_lines(formset),
                issue=action == "issue",
                submission_id=form.cleaned_data.get("submission_id"),
                **_header_data(form),
            )
        except InvoiceError as exc:
            form.add_error(None, exc.message)
        else:
            messages.success(
                request,
                "فاکتور صادر شد." if action == "issue" else "پیش‌نویس ذخیره شد.",
            )
            return redirect("invoicing:detail", invoice_id=invoice.id)
    return render(
        request,
        "invoicing/form.html",
        {
            "form": form,
            "formset": formset,
            "invoice": None,
            "templates": InvoiceTemplate.objects.filter(business=request.business),
            "recent_customers": recent_customers(request.business),
            "initial_step": _initial_step(form, formset),
        },
    )


@business_login_required
@require_capability(INVOICE_MANAGE)
@require_business_entitlement(ISSUE_INVOICES)
@require_http_methods(["GET", "POST"])
def invoice_edit(request: HttpRequest, invoice_id) -> HttpResponse:
    invoice = SalesInvoice.objects.filter(
        seller_business=request.business, pk=invoice_id
    ).prefetch_related("items").first()
    if invoice is None or invoice.status != SalesInvoice.Status.DRAFT:
        messages.error(request, "فقط پیش‌نویس خودتان قابل ویرایش است.")
        return redirect("invoicing:list")
    form = ManualInvoiceForm(
        request.POST or None,
        request.FILES or None,
        business=request.business,
        initial={**invoice_initial(invoice), "version": invoice.version},
    )
    formset = InvoiceLineFormSet(
        request.POST or None,
        prefix="lines",
        business=request.business,
        initial=invoice_line_initials(invoice),
    )
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        action = request.POST.get("action", "draft")
        try:
            invoice = update_draft_invoice(
                invoice=invoice,
                membership=request.membership,
                lines=_submitted_lines(formset),
                expected_version=form.cleaned_data.get("version"),
                **_header_data(form),
            )
            if action == "issue":
                invoice = issue_invoice(invoice=invoice, membership=request.membership)
        except InvoiceError as exc:
            form.add_error(None, exc.message)
        else:
            messages.success(
                request,
                "فاکتور صادر شد." if action == "issue" else "تغییرات پیش‌نویس ذخیره شد.",
            )
            return redirect("invoicing:detail", invoice_id=invoice.id)
    return render(
        request,
        "invoicing/form.html",
        {
            "form": form,
            "formset": formset,
            "invoice": invoice,
            "templates": [],
            "initial_step": _initial_step(form, formset),
        },
    )


@business_login_required
@require_capability(INVOICE_MANAGE)
@require_business_entitlement(ISSUE_INVOICES)
@require_POST
def invoice_preview(request: HttpRequest) -> HttpResponse:
    form = ManualInvoiceForm(request.POST, request.FILES, business=request.business)
    formset = InvoiceLineFormSet(
        request.POST, prefix="lines", business=request.business
    )
    if not (form.is_valid() and formset.is_valid()):
        return HttpResponse("برای پیش‌نمایش، خطاهای فرم را برطرف کنید.", status=422)
    header = _header_data(form)
    try:
        cleaned = _clean_lines(
            _submitted_lines(formset),
            seller_business=request.business,
            currency=header["currency"],
            display_unit=header["display_unit"],
            values_are_display=True,
        )
        calculated, totals = _calculate(
            cleaned,
            currency=header["currency"],
            display_unit=header["display_unit"],
            values_are_display=True,
            invoice_discount_type=header["invoice_discount_type"],
            invoice_discount_value=header["invoice_discount_value"],
            tax_amount=header["tax_amount"],
            shipping_amount=header["shipping_amount"],
            adjustment_amount=header["adjustment_amount"],
            paid_amount=header["paid_amount"],
        )
    except InvoiceError as exc:
        return HttpResponse(exc.message, status=422)
    document = build_preview_document(
        business=request.business, header=header, calculated=calculated, totals=totals
    )
    return HttpResponse(
        render_to_string(
            "invoicing/document.html",
            {"document": document, "document_mode": "preview"},
        )
    )


@business_login_required
@require_capability(INVOICE_MANAGE)
@require_business_entitlement(ISSUE_INVOICES)
@require_POST
def invoice_issue(request: HttpRequest, invoice_id) -> HttpResponse:
    invoice = SalesInvoice.objects.filter(
        seller_business=request.business, pk=invoice_id
    ).first()
    if invoice is None:
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
@require_business_entitlement(ISSUE_INVOICES)
@require_POST
def invoice_cancel(request: HttpRequest, invoice_id) -> HttpResponse:
    invoice = SalesInvoice.objects.filter(
        seller_business=request.business, pk=invoice_id
    ).first()
    if invoice is None:
        messages.error(request, "فاکتور یافت نشد.")
        return redirect("invoicing:list")
    form = InvoiceCancelForm(request.POST)
    if not form.is_valid():
        messages.error(request, "علت ابطال را وارد کنید.")
        return redirect("invoicing:detail", invoice_id=invoice.id)
    try:
        cancel_invoice(
            invoice=invoice,
            membership=request.membership,
            reason=form.cleaned_data["reason"],
        )
    except InvoiceError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "فاکتور باطل شد؛ مانده حساب تغییر نکرد.")
    return redirect("invoicing:detail", invoice_id=invoice.id)


@business_login_required
@require_capability(INVOICE_MANAGE)
@require_business_entitlement(ISSUE_INVOICES)
@require_POST
def invoice_delete_draft(request: HttpRequest, invoice_id) -> HttpResponse:
    invoice = SalesInvoice.objects.filter(
        seller_business=request.business, pk=invoice_id
    ).first()
    if invoice is None:
        messages.error(request, "پیش‌نویس یافت نشد.")
        return redirect("invoicing:list")
    try:
        delete_draft_invoice(invoice=invoice, membership=request.membership)
    except InvoiceError as exc:
        messages.error(request, exc.message)
        return redirect("invoicing:detail", invoice_id=invoice.id)
    messages.success(request, "پیش‌نویس حذف شد.")
    return redirect("invoicing:list")


@business_login_required
@require_capability(INVOICE_MANAGE)
@require_business_entitlement(ISSUE_INVOICES)
@require_POST
def invoice_create_replacement(request: HttpRequest, invoice_id) -> HttpResponse:
    invoice = SalesInvoice.objects.filter(
        seller_business=request.business, pk=invoice_id
    ).first()
    if invoice is None:
        messages.error(request, "فاکتور یافت نشد.")
        return redirect("invoicing:list")
    form = InvoiceCancelForm(request.POST)
    if not form.is_valid():
        messages.error(request, "علت اصلاح را وارد کنید.")
        return redirect("invoicing:detail", invoice_id=invoice.id)
    try:
        replacement = create_replacement_invoice(
            invoice=invoice,
            membership=request.membership,
            reason=form.cleaned_data["reason"],
        )
    except InvoiceError as exc:
        messages.error(request, exc.message)
        return redirect("invoicing:detail", invoice_id=invoice.id)
    messages.success(request, "اصل فاکتور باطل و پیش‌نویس جایگزین ساخته شد.")
    return redirect("invoicing:edit", invoice_id=replacement.id)


def _export(request, invoice_id, kind: str) -> HttpResponse:
    invoice = _invoice_or_redirect(request, invoice_id)
    if invoice is None:
        return redirect("invoicing:list")
    if not export_allowed(
        user_id=request.user.pk, invoice_id=invoice.pk, kind=kind
    ):
        return HttpResponse("تعداد درخواست خروجی زیاد است؛ یک دقیقه بعد دوباره تلاش کنید.", status=429)
    raw_number = invoice.number or str(invoice.id)[:8]
    safe_number = "".join(
        character for character in raw_number if character.isalnum() or character in "-_"
    )[:40] or str(invoice.id)[:8]
    try:
        if kind == "pdf":
            content, content_type, extension = render_pdf(invoice), "application/pdf", "pdf"
        else:
            content, content_type, extension = render_png(invoice)
    except DocumentRenderError as exc:
        logger.warning("Invoice export failed: %s", exc)
        return HttpResponse(str(exc), status=503)
    filename = f"sanga-invoice-{safe_number}.{extension}"
    response = HttpResponse(content, content_type=content_type)
    response.headers["Content-Disposition"] = content_disposition_header(True, filename)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "private, no-store"
    return response


@business_login_required
@require_capability(INVOICE_VIEW)
def invoice_pdf(request: HttpRequest, invoice_id) -> HttpResponse:
    return _export(request, invoice_id, "pdf")


@business_login_required
@require_capability(INVOICE_VIEW)
def invoice_image(request: HttpRequest, invoice_id) -> HttpResponse:
    return _export(request, invoice_id, "image")


@business_login_required
@require_capability(INVOICE_MANAGE)
@require_business_entitlement(ISSUE_INVOICES)
@require_POST
def invoice_duplicate(request: HttpRequest, invoice_id) -> HttpResponse:
    invoice = SalesInvoice.objects.filter(
        seller_business=request.business, pk=invoice_id
    ).prefetch_related("items").first()
    if invoice is None:
        messages.error(request, "فاکتور یافت نشد.")
        return redirect("invoicing:list")
    try:
        result = duplicate_invoice(invoice=invoice, membership=request.membership)
    except InvoiceError as exc:
        messages.error(request, exc.message)
        return redirect("invoicing:detail", invoice_id=invoice.id)
    messages.success(request, "یک پیش‌نویس جدید با شناسه مستقل ساخته شد.")
    return redirect("invoicing:edit", invoice_id=result.id)


@business_login_required
@require_capability(INVOICE_MANAGE)
@require_business_entitlement(ISSUE_INVOICES)
@require_POST
def invoice_save_template(request: HttpRequest, invoice_id) -> HttpResponse:
    invoice = SalesInvoice.objects.filter(
        seller_business=request.business, pk=invoice_id
    ).prefetch_related("items").first()
    form = InvoiceTemplateNameForm(request.POST)
    if invoice is None or not form.is_valid():
        messages.error(request, "نام قالب معتبر نیست.")
        return redirect("invoicing:list")
    try:
        save_as_template(
            invoice=invoice,
            name=form.cleaned_data["name"],
            membership=request.membership,
        )
    except InvoiceError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "قالب قابل‌استفاده مجدد ذخیره شد.")
    return redirect("invoicing:detail", invoice_id=invoice.id)


@business_login_required
@require_capability(INVOICE_MANAGE)
@require_business_entitlement(ISSUE_INVOICES)
@require_http_methods(["GET", "POST"])
def invoice_settings(request: HttpRequest) -> HttpResponse:
    settings_row = getattr(request.business, "invoice_settings", None)
    if settings_row is None:
        from .models import BusinessInvoiceSettings

        settings_row = BusinessInvoiceSettings(business=request.business)
    form = BusinessInvoiceSettingsForm(
        request.POST or None, request.FILES or None, instance=settings_row
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "تنظیمات فاکتور ذخیره شد.")
        return redirect("invoicing:settings")
    return render(
        request,
        "invoicing/settings.html",
        {
            "form": form,
            "settings_row": settings_row,
            "templates": InvoiceTemplate.objects.filter(business=request.business),
        },
    )


@business_login_required
@require_capability(INVOICE_MANAGE)
def invoice_asset(request: HttpRequest, kind: str) -> HttpResponse:
    settings_row = getattr(request.business, "invoice_settings", None)
    if settings_row is None:
        return HttpResponse(status=404)
    field = {"logo": settings_row.logo, "stamp": settings_row.stamp, "signature": settings_row.signature}.get(kind)
    if not field:
        return HttpResponse(status=404)
    field.open("rb")
    response = FileResponse(field.file, content_type="image/png")
    response.headers["Content-Disposition"] = "inline"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "private, max-age=300"
    return response


@business_login_required
@require_capability(INVOICE_MANAGE)
@require_business_entitlement(ISSUE_INVOICES)
@require_POST
def invoice_template_delete(request: HttpRequest, template_id) -> HttpResponse:
    template = InvoiceTemplate.objects.filter(
        business=request.business, pk=template_id
    ).first()
    if template:
        template.delete()
        messages.success(request, "قالب حذف شد.")
    return redirect("invoicing:settings")
