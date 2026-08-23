from __future__ import annotations

import logging
import uuid

from django.contrib import messages
from django.db import models
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from apps.businesses.decorators import business_login_required
from apps.core.pagination import paginate
from apps.inventory.filters import effective_price_bounds
from apps.inventory.forms import ItemFilterForm

from .models import PartnerInquiry
from .selectors import filter_marketplace_lots, get_marketplace_lot, marketplace_lots_for
from .services import (
    MarketplaceError,
    b2b_price_context,
    convert_inquiry_to_invoice,
    create_grouped_inquiries,
    marketplace_lot_card,
    respond_to_inquiry,
)

logger = logging.getLogger(__name__)


@business_login_required
def marketplace_home(request: HttpRequest) -> HttpResponse:
    if not request.business:
        return redirect("businesses:no_business")

    form = ItemFilterForm(request.GET or None)
    spec = form.to_spec()
    base = marketplace_lots_for(request.business)
    qs = filter_marketplace_lots(base, spec=spec)
    minimum, maximum = effective_price_bounds(base, spec=spec, audience="colleague")
    page = paginate(request, qs)
    cards = [marketplace_lot_card(lot, request.business) for lot in page.object_list]
    return render(
        request,
        "marketplace/home.html",
        {
            "filter_form": form,
            "cards": cards,
            "page": page,
            "price_bounds": {"minimum": minimum, "maximum": maximum},
            "submission_id": uuid.uuid4(),
        },
    )


@business_login_required
def marketplace_lot_detail(request: HttpRequest, lot_id) -> HttpResponse:
    if not request.business:
        return redirect("businesses:no_business")
    lot = get_marketplace_lot(request.business, lot_id)
    if lot is None:
        messages.error(request, "این محصول در بازار همکاران قابل مشاهده نیست.")
        return redirect("marketplace:home")

    from apps.inventory.freshness import stock_view

    return render(
        request,
        "marketplace/lot_detail.html",
        {
            "lot": lot,
            "product": lot.product,
            "supplier": lot.business,
            "price": b2b_price_context(lot, request.business),
            "stock": stock_view(lot),
            "media_items": lot.media.all(),
            "submission_id": uuid.uuid4(),
        },
    )


@business_login_required
@require_POST
def inquiry_create(request: HttpRequest) -> HttpResponse:
    selected = request.POST.getlist("lot")
    selections = [{"lot_id": lot_id, "quantity": request.POST.get(f"quantity_{lot_id}", "1")} for lot_id in selected]
    try:
        batch = create_grouped_inquiries(
            buyer_business=request.business,
            user=request.user,
            selections=selections,
            submission_id=request.POST.get("submission_id") or None,
            note=request.POST.get("note", ""),
        )
    except MarketplaceError as exc:
        messages.error(request, exc.message)
        return redirect("marketplace:home")
    messages.success(
        request,
        f"استعلام برای {batch.inquiries.count()} فروشنده، جداگانه و هم‌زمان ارسال شد.",
    )
    return redirect("marketplace:inquiries")


@business_login_required
def inquiry_list(request: HttpRequest) -> HttpResponse:
    inquiries = (
        PartnerInquiry.objects.filter(
            models.Q(buyer_business=request.business) | models.Q(seller_business=request.business)
        )
        .select_related("buyer_business", "seller_business", "converted_invoice")
        .prefetch_related("items")
    )
    return render(request, "marketplace/inquiry_list.html", {"inquiries": inquiries})


@business_login_required
@require_http_methods(["GET", "POST"])
def inquiry_detail(request: HttpRequest, inquiry_id) -> HttpResponse:
    inquiry = (
        PartnerInquiry.objects.filter(pk=inquiry_id)
        .filter(models.Q(buyer_business=request.business) | models.Q(seller_business=request.business))
        .select_related("buyer_business", "seller_business", "converted_invoice")
        .prefetch_related("items")
        .first()
    )
    if inquiry is None:
        messages.error(request, "استعلام یافت نشد.")
        return redirect("marketplace:inquiries")
    if request.method == "POST":
        offers = {
            str(item.id): {
                "quantity": request.POST.get(f"quantity_{item.id}"),
                "unit_price": request.POST.get(f"price_{item.id}"),
                "note": request.POST.get(f"note_{item.id}", ""),
            }
            for item in inquiry.items.all()
        }
        try:
            respond_to_inquiry(
                inquiry=inquiry,
                membership=request.membership,
                offers=offers,
                note=request.POST.get("seller_note", ""),
            )
        except MarketplaceError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(request, "پاسخ استعلام ثبت شد.")
        return redirect("marketplace:inquiry_detail", inquiry_id=inquiry.id)
    return render(
        request,
        "marketplace/inquiry_detail.html",
        {"inquiry": inquiry, "is_seller": inquiry.seller_business_id == request.business.id},
    )


@business_login_required
@require_POST
def inquiry_convert(request: HttpRequest, inquiry_id) -> HttpResponse:
    inquiry = PartnerInquiry.objects.filter(pk=inquiry_id, seller_business=request.business).first()
    if inquiry is None:
        messages.error(request, "استعلام یافت نشد.")
        return redirect("marketplace:inquiries")
    try:
        invoice = convert_inquiry_to_invoice(inquiry=inquiry, membership=request.membership)
    except MarketplaceError as exc:
        messages.error(request, exc.message)
        return redirect("marketplace:inquiry_detail", inquiry_id=inquiry.id)
    messages.success(request, "پیش‌نویس فاکتور از پاسخ استعلام ساخته شد.")
    return redirect("invoicing:edit", invoice_id=invoice.id)
