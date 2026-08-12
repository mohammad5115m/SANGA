from __future__ import annotations

import logging

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from apps.businesses.decorators import business_login_required
from apps.inquiries.models import Inquiry
from apps.inquiries.services import InquiryError, create_inquiry

from .forms import MarketplaceFilterForm, SaveSearchForm
from .selectors import (
    filter_marketplace_lots,
    get_marketplace_lot,
    marketplace_lots_for,
    saved_searches_for,
)
from .services import MarketplaceError, b2b_price_context, marketplace_lot_card, save_search

logger = logging.getLogger(__name__)


@business_login_required
def marketplace_home(request: HttpRequest) -> HttpResponse:
    if not request.business:
        return redirect("businesses:no_business")

    form = MarketplaceFilterForm(request.GET or None)
    qs = marketplace_lots_for(request.business)
    if form.is_valid():
        qs = filter_marketplace_lots(
            qs,
            q=form.cleaned_data.get("q", ""),
            stone_type=form.cleaned_data.get("stone_type", ""),
            color=form.cleaned_data.get("color", ""),
            only_urgent=bool(form.cleaned_data.get("only_urgent")),
            min_qty=form.cleaned_data.get("min_qty", ""),
        )
    cards = [marketplace_lot_card(lot, request.business) for lot in qs[:80]]
    save_form = SaveSearchForm()
    return render(
        request,
        "marketplace/home.html",
        {
            "filter_form": form,
            "save_form": save_form,
            "cards": cards,
            "saved_searches": saved_searches_for(request.business, request.user)[:10],
        },
    )


@business_login_required
@require_http_methods(["GET", "POST"])
def marketplace_lot_detail(request: HttpRequest, lot_id) -> HttpResponse:
    if not request.business:
        return redirect("businesses:no_business")
    lot = get_marketplace_lot(request.business, lot_id)
    if lot is None:
        messages.error(request, "این محموله در شبکه همکاران قابل مشاهده نیست.")
        return redirect("marketplace:home")

    from apps.catalog.forms import InquiryForm

    form = InquiryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            create_inquiry(
                business=lot.business,
                lot=lot,
                name=form.cleaned_data["name"],
                phone=form.cleaned_data["phone"],
                message=form.cleaned_data.get("message", ""),
                source=Inquiry.Source.MARKETPLACE,
                requester=request.user,
            )
        except InquiryError as exc:
            form.add_error(None, exc.message)
        else:
            messages.success(request, "استعلام برای تأمین‌کننده ارسال شد.")
            return redirect("marketplace:lot_detail", lot_id=lot.id)

    return render(
        request,
        "marketplace/lot_detail.html",
        {
            "lot": lot,
            "product": lot.product,
            "supplier": lot.business,
            "price": b2b_price_context(lot, request.business),
            "media_items": [m for m in lot.media.all() if m.kind == "image"],
            "inquiry_form": form,
        },
    )


@business_login_required
@require_POST
def save_current_search(request: HttpRequest) -> HttpResponse:
    if not request.business:
        return redirect("businesses:no_business")
    form = SaveSearchForm(request.POST)
    filters = MarketplaceFilterForm(request.POST)
    if form.is_valid() and filters.is_valid():
        try:
            save_search(
                business=request.business,
                user=request.user,
                name=form.cleaned_data["name"],
                notify_enabled=form.cleaned_data.get("notify_enabled", True),
                query={
                    "q": filters.cleaned_data.get("q", ""),
                    "stone_type": filters.cleaned_data.get("stone_type", ""),
                    "color": filters.cleaned_data.get("color", ""),
                    "min_qty": filters.cleaned_data.get("min_qty", ""),
                    "only_urgent": bool(filters.cleaned_data.get("only_urgent")),
                },
            )
        except MarketplaceError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(request, "جستجو ذخیره شد.")
    else:
        messages.error(request, "ذخیره جستجو ممکن نشد.")
    return redirect("marketplace:home")
