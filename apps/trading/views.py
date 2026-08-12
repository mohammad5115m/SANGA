from __future__ import annotations

import logging

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from apps.businesses.decorators import business_login_required, require_capability
from apps.businesses.permissions import PURCHASE_REQUEST, SALE_FINALIZE
from apps.inventory.policy import get_eligible_item

from .forms import FinalizeSaleForm, PurchaseRequestForm, PurchaseRequestResponseForm
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
    respond_to_purchase_request,
)

logger = logging.getLogger(__name__)

ROWS = 60

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
    return render(
        request,
        "trading/sent_list.html",
        {"requests": qs[:ROWS], "status": status, "status_filters": STATUS_FILTERS},
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
    return render(
        request,
        "trading/received_list.html",
        {"requests": qs[:ROWS], "status": status, "status_filters": STATUS_FILTERS},
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


# --- trades -------------------------------------------------------------------


@business_login_required
@require_capability(PURCHASE_REQUEST)
def trade_list(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "trading/trade_list.html",
        {"trades": trades_for_seller(request.business)[:ROWS]},
    )


@business_login_required
@require_capability(PURCHASE_REQUEST)
def trade_detail(request: HttpRequest, trade_id) -> HttpResponse:
    from .selectors import get_trade

    trade = get_trade(request.business, trade_id)
    if trade is None:
        messages.error(request, "معامله یافت نشد.")
        return redirect("trading:trade_list")

    return render(
        request,
        "trading/trade_detail.html",
        {"trade": trade, "is_seller": trade.seller_business_id == request.business.id},
    )
