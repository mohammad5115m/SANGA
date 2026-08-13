from __future__ import annotations

import logging

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from apps.businesses.decorators import business_login_required, require_capability
from apps.businesses.permissions import PURCHASE_REQUEST, SALE_FINALIZE
from apps.core.pagination import ROW_PAGE_SIZE, paginate
from apps.inventory.policy import get_eligible_item
from apps.invoicing.services import InvoiceError, create_invoice_for_trade

from .forms import DirectSaleForm, FinalizeSaleForm, PurchaseRequestForm, PurchaseRequestResponseForm
from .models import PurchaseRequest
from .selectors import (
    filter_requests,
    get_received_request,
    get_sent_request,
    received_requests,
    sent_requests,
    trades_for_seller,
)
from .services import (
    TradingError,
    cancel_purchase_request,
    create_purchase_request,
    finalize_sale,
    record_direct_sale,
    respond_to_purchase_request,
)

logger = logging.getLogger(__name__)

STATUS_FILTERS = (
    ("", "همه"),
    ("open", "در جریان"),
    (PurchaseRequest.Status.SENT, "در انتظار پاسخ"),
    (PurchaseRequest.Status.ACCEPTED, "توافق شده"),
    (PurchaseRequest.Status.COMPLETED, "فروش نهایی شد"),
    (PurchaseRequest.Status.REJECTED, "رد شده"),
)


# --- buyer side ---------------------------------------------------------------


@business_login_required
@require_capability(PURCHASE_REQUEST)
@require_http_methods(["GET", "POST"])
def request_create(request: HttpRequest, item_id) -> HttpResponse:
    """«درخواست خرید» from a marketplace product page."""
    item = get_eligible_item(audience="colleague", viewer_business=request.business, item_id=item_id)
    if item is None:
        messages.error(request, "این محصول برای خرید در دسترس نیست.")
        return redirect("marketplace:home")

    form = PurchaseRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            purchase_request = create_purchase_request(
                buyer_business=request.business,
                membership=request.membership,
                item=item,
                requested_qty_sqm=form.cleaned_data["requested_qty_sqm"],
                proposed_unit_price=form.cleaned_data.get("proposed_unit_price"),
                buyer_note=form.cleaned_data.get("buyer_note", ""),
            )
        except TradingError as exc:
            form.add_error(None, exc.message)
        else:
            messages.success(request, "درخواست خرید برای فروشنده ارسال شد.")
            return redirect("trading:sent_detail", request_id=purchase_request.id)

    from apps.marketplace.services import b2b_price_context

    return render(
        request,
        "trading/request_form.html",
        {"item": item, "form": form, "price": b2b_price_context(item, request.business)},
    )


@business_login_required
@require_capability(PURCHASE_REQUEST)
def sent_list(request: HttpRequest) -> HttpResponse:
    status = request.GET.get("status", "")
    qs = filter_requests(sent_requests(request.business), status=status)
    page = paginate(request, qs, per_page=ROW_PAGE_SIZE)
    return render(
        request,
        "trading/sent_list.html",
        {"requests": page.object_list, "page": page, "status": status, "status_filters": STATUS_FILTERS},
    )


@business_login_required
@require_capability(PURCHASE_REQUEST)
@require_http_methods(["GET", "POST"])
def sent_detail(request: HttpRequest, request_id) -> HttpResponse:
    purchase_request = get_sent_request(request.business, request_id)
    if purchase_request is None:
        messages.error(request, "درخواست یافت نشد.")
        return redirect("trading:sent_list")

    if request.method == "POST":
        try:
            cancel_purchase_request(request=purchase_request, membership=request.membership)
        except TradingError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(request, "درخواست لغو شد.")
        return redirect("trading:sent_detail", request_id=purchase_request.id)

    return render(request, "trading/sent_detail.html", {"pr": purchase_request})


# --- seller side --------------------------------------------------------------


@business_login_required
@require_capability(PURCHASE_REQUEST)
def received_list(request: HttpRequest) -> HttpResponse:
    status = request.GET.get("status", "")
    qs = filter_requests(received_requests(request.business), status=status)
    page = paginate(request, qs, per_page=ROW_PAGE_SIZE)
    return render(
        request,
        "trading/received_list.html",
        {"requests": page.object_list, "page": page, "status": status, "status_filters": STATUS_FILTERS},
    )


@business_login_required
@require_capability(PURCHASE_REQUEST)
@require_http_methods(["GET", "POST"])
def received_detail(request: HttpRequest, request_id) -> HttpResponse:
    purchase_request = get_received_request(request.business, request_id)
    if purchase_request is None:
        messages.error(request, "درخواست یافت نشد.")
        return redirect("trading:received_list")

    form = PurchaseRequestResponseForm(
        request.POST or None,
        initial={
            "final_qty_sqm": purchase_request.requested_qty_sqm,
            "final_unit_price": purchase_request.proposed_unit_price,
        },
    )
    if request.method == "POST" and form.is_valid():
        try:
            respond_to_purchase_request(
                request=purchase_request,
                membership=request.membership,
                accept=request.POST.get("decision") == "accept",
                final_qty_sqm=form.cleaned_data.get("final_qty_sqm"),
                final_unit_price=form.cleaned_data.get("final_unit_price"),
                seller_note=form.cleaned_data.get("seller_note", ""),
            )
        except TradingError as exc:
            form.add_error(None, exc.message)
        else:
            messages.success(request, "پاسخ شما ثبت شد.")
            return redirect("trading:received_detail", request_id=purchase_request.id)

    return render(
        request,
        "trading/received_detail.html",
        {
            "pr": purchase_request,
            "form": form,
            "can_finalize": request.membership.has_capability(SALE_FINALIZE),
        },
    )


@business_login_required
@require_capability(SALE_FINALIZE)
@require_http_methods(["GET", "POST"])
def finalize(request: HttpRequest, request_id) -> HttpResponse:
    """«نهایی کردن فروش» — a separate, deliberate step after acceptance."""
    purchase_request = get_received_request(request.business, request_id)
    if purchase_request is None:
        messages.error(request, "درخواست یافت نشد.")
        return redirect("trading:received_list")

    if purchase_request.status == PurchaseRequest.Status.COMPLETED:
        messages.info(request, "این فروش قبلاً نهایی شده است.")
        return redirect("trading:received_detail", request_id=purchase_request.id)

    form = FinalizeSaleForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            trade = finalize_sale(
                request=purchase_request,
                membership=request.membership,
                note=form.cleaned_data.get("note", ""),
            )
        except TradingError as exc:
            form.add_error(None, exc.message)
        else:
            messages.success(request, "فروش نهایی شد و در حساب همکار ثبت گردید.")
            return redirect("trading:trade_detail", trade_id=trade.id)

    return render(request, "trading/finalize.html", {"pr": purchase_request, "form": form})


@business_login_required
@require_capability(SALE_FINALIZE)
@require_http_methods(["GET", "POST"])
def direct_sale(request: HttpRequest) -> HttpResponse:
    """«ثبت فروش مستقیم» — record a phone or counter sale.

    The authoritative entry point for a sale that never went through a purchase
    request. It creates the Trade, posts both parties' books and issues the
    invoice in one transaction, so a colleague sale can never exist as a document
    with no matching balance.
    """
    form = DirectSaleForm(request.POST or None, business=request.business)
    if request.method == "POST" and form.is_valid():
        try:
            trade = record_direct_sale(
                seller_business=request.business,
                membership=request.membership,
                item=form.cleaned_data.get("item"),
                quantity_sqm=form.cleaned_data["quantity_sqm"],
                unit_price=form.cleaned_data["unit_price"],
                buyer_business=form.cleaned_data.get("buyer_business"),
                customer_name=form.cleaned_data.get("customer_name", ""),
                customer_phone=form.cleaned_data.get("customer_phone", ""),
                product_name=form.cleaned_data.get("product_name", ""),
                note=form.cleaned_data.get("note", ""),
                submission_id=form.cleaned_data["submission_id"],
            )
        except TradingError as exc:
            form.add_error(None, exc.message)
        else:
            messages.success(request, "فروش ثبت شد.")
            return redirect("trading:trade_detail", trade_id=trade.id)

    return render(request, "trading/direct_sale.html", {"form": form})


# --- trades -------------------------------------------------------------------


@business_login_required
@require_capability(PURCHASE_REQUEST)
def trade_list(request: HttpRequest) -> HttpResponse:
    page = paginate(request, trades_for_seller(request.business), per_page=ROW_PAGE_SIZE)
    return render(
        request,
        "trading/trade_list.html",
        {"trades": page.object_list, "page": page},
    )


@business_login_required
@require_capability(PURCHASE_REQUEST)
def trade_detail(request: HttpRequest, trade_id) -> HttpResponse:
    from .selectors import get_trade

    trade = get_trade(request.business, trade_id)
    if trade is None:
        messages.error(request, "معامله یافت نشد.")
        return redirect("trading:trade_list")

    is_seller = trade.seller_business_id == request.business.id
    return render(
        request,
        "trading/trade_detail.html",
        {
            "trade": trade,
            "is_seller": is_seller,
            "invoice": trade.invoices.first(),
            "can_create_invoice": is_seller and request.membership.has_capability(SALE_FINALIZE),
        },
    )


@business_login_required
@require_capability(SALE_FINALIZE)
@require_POST
def trade_create_invoice(request: HttpRequest, trade_id) -> HttpResponse:
    """Issue the document for a sale that ended up without one.

    Invoice creation is a consequence of finalizing, and it is best-effort so a
    failure there can never roll back a completed sale. That leaves a narrow gap
    — a plan that lapsed mid-flow, a transient error — where a finalized trade
    has no document. Before this there was no way out of it but the admin.
    """
    from .selectors import get_trade

    trade = get_trade(request.business, trade_id)
    if trade is None or trade.seller_business_id != request.business.id:
        messages.error(request, "معامله یافت نشد.")
        return redirect("trading:trade_list")

    try:
        invoice = create_invoice_for_trade(trade=trade, membership=request.membership)
    except InvoiceError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, f"فاکتور {invoice.number} ساخته شد.")
    return redirect("trading:trade_detail", trade_id=trade.id)
