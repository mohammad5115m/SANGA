from __future__ import annotations

import logging

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import FileResponse, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import content_disposition_header
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_http_methods, require_POST

from apps.businesses.decorators import (
    business_login_required,
    require_business_entitlement,
    require_capability,
)
from apps.businesses.directory import colleague_businesses
from apps.businesses.entitlements import ISSUE_INVOICES, has_entitlement
from apps.businesses.permissions import (
    BUSINESS_SIGNATURE_MANAGE,
    CHEQUE_MANAGE,
    COUNTERPARTY_LINK_APPROVE,
    COUNTERPARTY_LINK_PROPOSE,
    INVOICE_CONFIRM,
    INVOICE_CREATE,
    INVOICE_MANAGE,
    INVOICE_OFFLINE_APPROVE,
    INVOICE_SEND,
    INVOICE_VIEW,
)
from apps.core.pagination import ROW_PAGE_SIZE, paginate

from .calculations import DISCOUNT_AMOUNT, to_display_amount
from .documents import build_invoice_document, build_preview_document
from .forms import (
    BusinessInvoiceSettingsForm,
    ChequeStatusForm,
    InvoiceCancelForm,
    InvoiceLineFormSet,
    InvoiceTemplateNameForm,
    ManualInvoiceForm,
    OfflineApprovalForm,
    PartnerDecisionForm,
    PersonalSignatureForm,
    invoice_initial,
    invoice_line_initials,
    new_submission_id,
)
from .models import (
    ChequeReceivable,
    CounterpartyLinkProposal,
    InvoiceRevision,
    InvoiceTemplate,
    LocalCounterparty,
    SalesInvoice,
    UserInvoiceSignature,
)
from .partner_services import (
    cancel_counterparty_link,
    cancel_pending_partner_invoice,
    change_cheque_status,
    confirm_local_invoice_offline,
    confirm_partner_invoice,
    create_partner_draft,
    decide_counterparty_link,
    propose_counterparty_link,
    reject_partner_invoice,
    resolve_local_counterparty,
    send_partner_invoice,
    update_partner_draft,
)
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
    return request.membership.has_capability(INVOICE_CREATE) and has_entitlement(request.business, ISSUE_INVOICES)


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
        "counterparty_mode",
        "buyer_business",
        "local_counterparty",
        "local_name",
        "local_phone",
        "local_address",
        "settlement_method",
        "cash_amount",
        "credit_amount",
        "cheque_amount",
        "cheque_reference",
        "cheque_bank",
        "cheque_due_date",
        "cheque_drawer",
        "notes",
        "payment_terms",
        "buyer_signature",
        "remove_buyer_signature",
    )
    # Document appearance always comes from BusinessInvoiceSettings.  The
    # creation form no longer exposes or honors per-invoice appearance choices.
    return {field: form.cleaned_data.get(field) for field in fields}


def _cheque_details(header: dict) -> dict:
    due = header.get("cheque_due_date")
    return {
        "reference_number": (header.get("cheque_reference") or "").strip(),
        "bank": (header.get("cheque_bank") or "").strip(),
        "due_date": due.isoformat() if due else "",
        "drawer_name": (header.get("cheque_drawer") or "").strip(),
    }


def _partner_header(header: dict) -> dict:
    return {
        key: header.get(key)
        for key in (
            "issue_date",
            "currency",
            "display_unit",
            "invoice_discount_type",
            "invoice_discount_value",
            "tax_amount",
            "shipping_amount",
            "adjustment_amount",
            "settlement_method",
            "cash_amount",
            "credit_amount",
            "cheque_amount",
            "notes",
            "payment_terms",
        )
    } | {"cheque_details": _cheque_details(header)}


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
    current_settings = getattr(request.business, "invoice_settings", None)
    return render(
        request,
        "invoicing/detail.html",
        {
            "invoice": invoice,
            "document": build_invoice_document(invoice),
            "is_seller": invoice.seller_business_id == request.business.id,
            "can_manage": _can_manage_invoices(request),
            "can_cancel": request.membership.has_capability(INVOICE_MANAGE),
            "can_confirm": request.membership.has_capability(INVOICE_CONFIRM),
            "can_send": request.membership.has_capability(INVOICE_SEND),
            "can_offline_approve": request.membership.has_capability(INVOICE_OFFLINE_APPROVE),
            "can_manage_cheques": request.membership.has_capability(CHEQUE_MANAGE),
            "has_business_signature": bool(current_settings and current_settings.signature),
            "has_personal_signature": UserInvoiceSignature.objects.filter(user=request.user).exists(),
            "decision_form": PartnerDecisionForm(),
            "offline_form": OfflineApprovalForm(
                initial={"confirmed_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M")}
            ),
            "cheque_form": ChequeStatusForm(),
            "cancel_form": InvoiceCancelForm(),
            "template_form": InvoiceTemplateNameForm(initial={"name": f"قالب {invoice.buyer_name}"}),
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
        **{
            key: payload.get(key, "")
            for key in ("customer_name", "customer_phone", "buyer_address", "notes", "payment_terms")
        },
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

    requested_item_ids = [source.get("item_id") for source in payload.get("lines", []) if source.get("item_id")]
    usable_item_ids = {
        str(item_id)
        for item_id in InventoryLot.objects.filter(business=template.business, id__in=requested_item_ids).values_list(
            "id", flat=True
        )
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
                "item": (source.get("item_id") if str(source.get("item_id")) in usable_item_ids else None),
                "unit_price": display(source.get("unit_price", 0)),
                "discount_value": discount,
                "ORDER": source.get("sort_order", len(lines)),
            }
        )
    return header, lines


def _shared_item_initial(business, item_id):
    """Build a safe invoice handoff from one product owned by the seller."""
    from apps.inventory.selectors import get_business_lot
    from apps.pricing.services import resolve_visible_prices

    try:
        lot = get_business_lot(business, item_id)
    except (ValidationError, TypeError, ValueError):
        lot = None
    if lot is None:
        return None, None

    header = {"counterparty_mode": SalesInvoice.Counterparty.BUSINESS}
    price = resolve_visible_prices(lot, "owner_staff").get("b2b")
    unit_price = None
    if price is not None and price.amount is not None:
        currency = price.currency or SalesInvoice.Currency.IRR
        settings_row = getattr(business, "invoice_settings", None)
        display_unit = currency
        if settings_row is not None and settings_row.default_currency == currency:
            display_unit = settings_row.default_display_unit
        header.update({"currency": currency, "display_unit": display_unit})
        unit_price = to_display_amount(
            price.amount,
            currency=currency,
            display_unit=display_unit,
        )

    return header, [
        {
            "item": lot.id,
            "product_name": lot.product.commercial_name,
            "stone_type": lot.product.stone.name,
            "quantity": lot.min_sale_qty or 1,
            "unit": "متر مربع",
            "unit_price": unit_price,
            "ORDER": 0,
        }
    ]


@business_login_required
@require_capability(INVOICE_CREATE)
@require_business_entitlement(ISSUE_INVOICES)
@require_http_methods(["GET", "POST"])
def invoice_create(request: HttpRequest) -> HttpResponse:
    initial = None
    line_initial = None
    template_id = request.GET.get("template")
    if template_id:
        try:
            template = InvoiceTemplate.objects.filter(business=request.business, pk=template_id).first()
        except (ValidationError, TypeError, ValueError):
            template = None
        if template:
            try:
                initial, line_initial = _template_initial(template)
            except (TypeError, ValueError):
                messages.warning(request, "داده‌های این قالب معتبر نیست؛ فرم خالی باز شد.")
    elif request.method == "GET" and request.GET.get("item"):
        initial, line_initial = _shared_item_initial(
            request.business,
            request.GET.get("item"),
        )
        if line_initial:
            messages.info(
                request,
                "محصول اشتراک‌گذاری‌شده دقیقاً در فاکتور قرار گرفت؛ خریدار و متراژ نهایی را بررسی کنید.",
            )
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
        header = _header_data(form)
        try:
            if header["counterparty_mode"] == SalesInvoice.Counterparty.CUSTOMER:
                invoice = create_manual_invoice(
                    business=request.business,
                    membership=request.membership,
                    lines=_submitted_lines(formset),
                    issue=action == "issue",
                    submission_id=form.cleaned_data.get("submission_id"),
                    **{
                        key: header[key]
                        for key in (
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
                    },
                )
            else:
                local = header.get("local_counterparty")
                if header["counterparty_mode"] == SalesInvoice.Counterparty.LOCAL:
                    local = resolve_local_counterparty(
                        business=request.business,
                        membership=request.membership,
                        local_counterparty=local,
                        name=header.get("local_name"),
                        phone=header.get("local_phone"),
                        address=header.get("local_address"),
                    )
                invoice = create_partner_draft(
                    business=request.business,
                    membership=request.membership,
                    lines=_submitted_lines(formset),
                    buyer_business=header.get("buyer_business"),
                    local_counterparty=local,
                    submission_id=form.cleaned_data.get("submission_id"),
                    **_partner_header(header),
                )
                if action == "issue" and invoice.counterparty_type == SalesInvoice.Counterparty.BUSINESS:
                    invoice = send_partner_invoice(invoice=invoice, membership=request.membership)
        except InvoiceError as exc:
            form.add_error(None, exc.message)
        else:
            messages.success(
                request,
                "فاکتور ارسال/نهایی شد." if action == "issue" else "پیش‌نویس ذخیره شد.",
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
        },
    )


@business_login_required
@require_capability(INVOICE_CREATE)
@require_business_entitlement(ISSUE_INVOICES)
@require_http_methods(["GET", "POST"])
def invoice_edit(request: HttpRequest, invoice_id) -> HttpResponse:
    invoice = (
        SalesInvoice.objects.filter(seller_business=request.business, pk=invoice_id).prefetch_related("items").first()
    )
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
        header = _header_data(form)
        try:
            if invoice.counterparty_type == SalesInvoice.Counterparty.CUSTOMER:
                invoice = update_draft_invoice(
                    invoice=invoice,
                    membership=request.membership,
                    lines=_submitted_lines(formset),
                    expected_version=form.cleaned_data.get("version"),
                    **header,
                )
            else:
                invoice = update_partner_draft(
                    invoice=invoice,
                    membership=request.membership,
                    lines=_submitted_lines(formset),
                    **_partner_header(header),
                )
            if action == "issue":
                invoice = (
                    issue_invoice(invoice=invoice, membership=request.membership)
                    if invoice.counterparty_type == SalesInvoice.Counterparty.CUSTOMER
                    else send_partner_invoice(invoice=invoice, membership=request.membership)
                    if invoice.counterparty_type == SalesInvoice.Counterparty.BUSINESS
                    else invoice
                )
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
        },
    )


@business_login_required
@require_capability(INVOICE_CREATE)
@require_business_entitlement(ISSUE_INVOICES)
@require_POST
def invoice_preview(request: HttpRequest) -> HttpResponse:
    form = ManualInvoiceForm(request.POST, request.FILES, business=request.business)
    formset = InvoiceLineFormSet(request.POST, prefix="lines", business=request.business)
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
    document = build_preview_document(business=request.business, header=header, calculated=calculated, totals=totals)
    return HttpResponse(
        render_to_string(
            "invoicing/document.html",
            {"document": document, "document_mode": "preview"},
        )
    )


@business_login_required
@require_capability(INVOICE_CREATE)
@require_business_entitlement(ISSUE_INVOICES)
@require_POST
def invoice_issue(request: HttpRequest, invoice_id) -> HttpResponse:
    invoice = SalesInvoice.objects.filter(seller_business=request.business, pk=invoice_id).first()
    if invoice is None:
        messages.error(request, "فاکتور یافت نشد.")
        return redirect("invoicing:list")
    try:
        if invoice.counterparty_type == SalesInvoice.Counterparty.CUSTOMER:
            issue_invoice(invoice=invoice, membership=request.membership)
        elif invoice.counterparty_type == SalesInvoice.Counterparty.BUSINESS:
            send_partner_invoice(invoice=invoice, membership=request.membership)
        else:
            raise InvoiceError("همکار محلی باید با ثبت تأیید آفلاین نهایی شود.")
    except InvoiceError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "فاکتور صادر شد.")
    return redirect("invoicing:detail", invoice_id=invoice.id)


@business_login_required
@require_capability(INVOICE_CONFIRM)
@require_POST
def invoice_confirm(request: HttpRequest, invoice_id) -> HttpResponse:
    invoice = SalesInvoice.objects.filter(buyer_business=request.business, pk=invoice_id).first()
    if invoice is None:
        messages.error(request, "فاکتور دریافتی یافت نشد.")
        return redirect("invoicing:list")
    try:
        confirm_partner_invoice(invoice=invoice, membership=request.membership)
    except InvoiceError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "فاکتور تأیید و فروش نهایی شد.")
    return redirect("invoicing:detail", invoice_id=invoice.id)


@business_login_required
@require_capability(INVOICE_CONFIRM)
@require_POST
def invoice_reject(request: HttpRequest, invoice_id) -> HttpResponse:
    invoice = SalesInvoice.objects.filter(buyer_business=request.business, pk=invoice_id).first()
    form = PartnerDecisionForm(request.POST)
    if invoice is None:
        messages.error(request, "فاکتور دریافتی یافت نشد.")
        return redirect("invoicing:list")
    if not form.is_valid() or not (form.cleaned_data.get("reason") or "").strip():
        messages.error(request, "علت رد فاکتور را وارد کنید.")
        return redirect("invoicing:detail", invoice_id=invoice.id)
    try:
        reject_partner_invoice(
            invoice=invoice,
            membership=request.membership,
            reason=form.cleaned_data["reason"],
        )
    except InvoiceError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "فاکتور رد شد و همان فاکتور برای اصلاح به فرستنده برگشت.")
        return redirect("invoicing:list")
    return redirect("invoicing:detail", invoice_id=invoice.id)


@business_login_required
@require_capability(INVOICE_SEND)
@require_POST
def invoice_cancel_pending(request: HttpRequest, invoice_id) -> HttpResponse:
    invoice = SalesInvoice.objects.filter(seller_business=request.business, pk=invoice_id).first()
    form = PartnerDecisionForm(request.POST)
    if invoice is None:
        messages.error(request, "فاکتور یافت نشد.")
        return redirect("invoicing:list")
    try:
        cancel_pending_partner_invoice(
            invoice=invoice,
            membership=request.membership,
            reason=form.cleaned_data.get("reason", "") if form.is_valid() else "",
        )
    except InvoiceError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "فاکتور در انتظار توسط فرستنده لغو شد.")
    return redirect("invoicing:detail", invoice_id=invoice.id)


@business_login_required
@require_capability(INVOICE_OFFLINE_APPROVE)
@require_POST
def invoice_offline_confirm(request: HttpRequest, invoice_id) -> HttpResponse:
    invoice = SalesInvoice.objects.filter(seller_business=request.business, pk=invoice_id).first()
    form = OfflineApprovalForm(request.POST, request.FILES)
    if invoice is None:
        messages.error(request, "فاکتور یافت نشد.")
        return redirect("invoicing:list")
    if not form.is_valid():
        messages.error(request, "اطلاعات تأیید آفلاین کامل یا معتبر نیست.")
        return redirect("invoicing:detail", invoice_id=invoice.id)
    try:
        confirm_local_invoice_offline(
            invoice=invoice,
            membership=request.membership,
            signer_name=form.cleaned_data["signer_name"],
            confirmed_at=form.cleaned_data["confirmed_at"],
            signature=form.cleaned_data["signature"],
            attested=form.cleaned_data["attested"],
        )
    except InvoiceError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "تأیید آفلاین ثبت و فروش نهایی شد.")
    return redirect("invoicing:detail", invoice_id=invoice.id)


@business_login_required
@require_capability(CHEQUE_MANAGE)
@require_POST
def cheque_status_update(request: HttpRequest, cheque_id) -> HttpResponse:
    cheque = (
        ChequeReceivable.objects.filter(invoice__seller_business=request.business, pk=cheque_id)
        .select_related("invoice")
        .first()
    )
    form = ChequeStatusForm(request.POST)
    if cheque is None:
        messages.error(request, "چک یافت نشد.")
        return redirect("invoicing:list")
    if form.is_valid():
        try:
            change_cheque_status(
                cheque=cheque,
                membership=request.membership,
                status=form.cleaned_data["status"],
                reason=form.cleaned_data["reason"],
            )
        except InvoiceError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(request, "وضعیت چک ثبت شد.")
    return redirect("invoicing:detail", invoice_id=cheque.invoice_id)


@business_login_required
@require_capability(INVOICE_MANAGE)
@require_business_entitlement(ISSUE_INVOICES)
@require_POST
def invoice_cancel(request: HttpRequest, invoice_id) -> HttpResponse:
    invoice = SalesInvoice.objects.filter(seller_business=request.business, pk=invoice_id).first()
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
@require_capability(INVOICE_CREATE)
@require_business_entitlement(ISSUE_INVOICES)
@require_POST
def invoice_delete_draft(request: HttpRequest, invoice_id) -> HttpResponse:
    invoice = SalesInvoice.objects.filter(seller_business=request.business, pk=invoice_id).first()
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
    invoice = SalesInvoice.objects.filter(seller_business=request.business, pk=invoice_id).first()
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
    if not export_allowed(user_id=request.user.pk, invoice_id=invoice.pk, kind=kind):
        return HttpResponse("تعداد درخواست خروجی زیاد است؛ یک دقیقه بعد دوباره تلاش کنید.", status=429)
    raw_number = invoice.number or str(invoice.id)[:8]
    safe_number = (
        "".join(character for character in raw_number if character.isalnum() or character in "-_")[:40]
        or str(invoice.id)[:8]
    )
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
@require_capability(INVOICE_CREATE)
@require_business_entitlement(ISSUE_INVOICES)
@require_POST
def invoice_duplicate(request: HttpRequest, invoice_id) -> HttpResponse:
    invoice = (
        SalesInvoice.objects.filter(seller_business=request.business, pk=invoice_id).prefetch_related("items").first()
    )
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
@require_capability(INVOICE_CREATE)
@require_business_entitlement(ISSUE_INVOICES)
@require_POST
def invoice_save_template(request: HttpRequest, invoice_id) -> HttpResponse:
    invoice = (
        SalesInvoice.objects.filter(seller_business=request.business, pk=invoice_id).prefetch_related("items").first()
    )
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
@require_capability(INVOICE_VIEW)
@require_business_entitlement(ISSUE_INVOICES)
@require_http_methods(["GET", "POST"])
def invoice_settings(request: HttpRequest) -> HttpResponse:
    can_manage_business_signature = request.membership.has_capability(BUSINESS_SIGNATURE_MANAGE)
    if request.method == "POST" and not can_manage_business_signature:
        messages.error(request, "فقط مالک یا مدیر مجاز می‌تواند امضای رسمی کسب‌وکار را تغییر دهد.")
        return redirect("invoicing:settings")
    settings_row = getattr(request.business, "invoice_settings", None)
    if settings_row is None:
        from .models import BusinessInvoiceSettings

        settings_row = BusinessInvoiceSettings(business=request.business)
    form = BusinessInvoiceSettingsForm(request.POST or None, request.FILES or None, instance=settings_row)
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
            "personal_signature": UserInvoiceSignature.objects.filter(user=request.user).first(),
            "personal_signature_form": PersonalSignatureForm(),
            "templates": InvoiceTemplate.objects.filter(business=request.business),
            "can_manage_business_signature": can_manage_business_signature,
        },
    )


@business_login_required
def counterparty_links(request: HttpRequest) -> HttpResponse:
    can_propose = request.membership.has_capability(COUNTERPARTY_LINK_PROPOSE)
    can_approve = request.membership.has_capability(COUNTERPARTY_LINK_APPROVE)
    if not (can_propose or can_approve):
        messages.error(request, "دسترسی لازم برای مدیریت اتصال همکاران را ندارید.")
        return redirect("businesses:dashboard")
    local_rows = (
        LocalCounterparty.objects.filter(owner_business=request.business)
        .select_related("linked_business")
        .prefetch_related("invoices", "ledger_entries")
    )
    outgoing = (
        CounterpartyLinkProposal.objects.filter(local_counterparty__owner_business=request.business)
        .select_related("local_counterparty", "target_business")
        .order_by("-created_at")
    )
    incoming = (
        CounterpartyLinkProposal.objects.filter(target_business=request.business)
        .select_related("local_counterparty", "local_counterparty__owner_business")
        .prefetch_related("local_counterparty__invoices", "local_counterparty__ledger_entries")
        .order_by("-created_at")
    )
    return render(
        request,
        "invoicing/counterparty_links.html",
        {
            "local_counterparties": local_rows,
            "targets": colleague_businesses(request.business),
            "outgoing_proposals": outgoing,
            "incoming_proposals": incoming,
            "can_propose": can_propose,
            "can_approve": can_approve,
        },
    )


@business_login_required
@require_capability(COUNTERPARTY_LINK_PROPOSE)
@require_POST
def counterparty_link_propose(request: HttpRequest) -> HttpResponse:
    local = LocalCounterparty.objects.filter(
        pk=request.POST.get("local_counterparty"), owner_business=request.business
    ).first()
    target = colleague_businesses(request.business).filter(pk=request.POST.get("target_business")).first()
    if local is None or target is None:
        messages.error(request, "همکار محلی یا کسب‌وکار مقصد معتبر نیست.")
        return redirect("invoicing:counterparty_links")
    try:
        propose_counterparty_link(local_counterparty=local, target=target, membership=request.membership)
    except InvoiceError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "پیشنهاد اتصال برای تأیید کسب‌وکار مقصد ارسال شد.")
    return redirect("invoicing:counterparty_links")


@business_login_required
@require_capability(COUNTERPARTY_LINK_APPROVE)
@require_POST
def counterparty_link_decide(request: HttpRequest, proposal_id) -> HttpResponse:
    proposal = CounterpartyLinkProposal.objects.filter(
        pk=proposal_id, target_business=request.business
    ).first()
    if proposal is None:
        messages.error(request, "پیشنهاد اتصال یافت نشد.")
        return redirect("invoicing:counterparty_links")
    try:
        decide_counterparty_link(
            proposal=proposal,
            membership=request.membership,
            approve=request.POST.get("decision") == "approve",
            reason=request.POST.get("reason", ""),
        )
    except InvoiceError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "تصمیم اتصال و انتقال سابقه ثبت شد.")
    return redirect("invoicing:counterparty_links")


@business_login_required
@require_capability(COUNTERPARTY_LINK_PROPOSE)
@require_POST
def counterparty_link_cancel(request: HttpRequest, proposal_id) -> HttpResponse:
    proposal = CounterpartyLinkProposal.objects.filter(
        pk=proposal_id, local_counterparty__owner_business=request.business
    ).first()
    if proposal is None:
        messages.error(request, "پیشنهاد اتصال یافت نشد.")
        return redirect("invoicing:counterparty_links")
    try:
        cancel_counterparty_link(proposal=proposal, membership=request.membership)
    except InvoiceError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "پیشنهاد اتصال لغو شد.")
    return redirect("invoicing:counterparty_links")


@business_login_required
@require_capability(INVOICE_VIEW)
@require_POST
def personal_signature_update(request: HttpRequest) -> HttpResponse:
    instance = UserInvoiceSignature.objects.filter(user=request.user).first()
    form = PersonalSignatureForm(request.POST, request.FILES, instance=instance)
    if not form.is_valid():
        messages.error(request, "فایل امضای شخصی معتبر نیست.")
        return redirect("invoicing:settings")
    image = form.cleaned_data.get("image")
    if image is False:
        if instance:
            instance.delete()
        messages.success(request, "امضای شخصی حذف شد.")
    else:
        signature = form.save(commit=False)
        signature.user = request.user
        signature.save()
        messages.success(request, "امضای شخصی ذخیره شد.")
    return redirect("invoicing:settings")


@business_login_required
@require_capability(INVOICE_VIEW)
def revision_signature_asset(request: HttpRequest, revision_id, kind: str) -> HttpResponse:
    revision = InvoiceRevision.objects.filter(pk=revision_id).select_related("invoice").first()
    if revision is None or request.business.id not in {
        revision.invoice.seller_business_id,
        revision.invoice.buyer_business_id,
    }:
        return HttpResponse(status=404)
    field = {
        "seller-business": revision.seller_business_signature,
        "seller-user": revision.seller_user_signature,
        "buyer-business": revision.buyer_business_signature,
        "buyer-user": revision.buyer_user_signature,
        "offline-buyer": revision.offline_buyer_signature,
    }.get(kind)
    if not field:
        return HttpResponse(status=404)
    field.open("rb")
    response = FileResponse(field.file, content_type="image/png")
    response.headers["Content-Disposition"] = "inline"
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@business_login_required
@require_capability(INVOICE_VIEW)
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
@require_capability(INVOICE_CREATE)
@require_business_entitlement(ISSUE_INVOICES)
@require_POST
def invoice_template_delete(request: HttpRequest, template_id) -> HttpResponse:
    template = InvoiceTemplate.objects.filter(business=request.business, pk=template_id).first()
    if template:
        template.delete()
        messages.success(request, "قالب حذف شد.")
    return redirect("invoicing:settings")
