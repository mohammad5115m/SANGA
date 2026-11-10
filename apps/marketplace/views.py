from __future__ import annotations

import logging

from django.contrib import messages
from django.db import models
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from apps.businesses.decorators import business_login_required
from apps.businesses.permissions import PRICES_EDIT
from apps.catalog.services import b2c_price_context
from apps.core.pagination import paginate
from apps.inventory.filters import effective_price_bounds
from apps.inventory.forms import MarketplaceItemFilterForm
from apps.inventory.selectors import lots_for_business
from apps.resale.models import ResaleOffer
from apps.resale.selectors import retailer_rules

from .models import PartnerInquiry
from .selectors import (
    filter_marketplace_lots,
    get_marketplace_lot,
    get_marketplace_lot_by_token,
    marketplace_lots_for,
)
from .services import b2b_price_context, marketplace_lot_card

logger = logging.getLogger(__name__)


def _partner_share_url(request: HttpRequest, lot) -> str:
    return request.build_absolute_uri(
        reverse("marketplace:shared_item", args=[lot.public_token])
    )


def _resale_choices(request, lots):
    if not lots or not request.membership or not request.membership.has_capability(PRICES_EDIT):
        return {}
    active_offers = ResaleOffer.objects.filter(
        rule_id=models.OuterRef("pk"), grant__retailer=request.business, grant__is_active=True, is_active=True,
    )
    rules = retailer_rules(request.business).filter(lot_id__in=[lot.pk for lot in lots]).annotate(
        has_offer=models.Exists(active_offers),
    )
    return {
        lot_id: {"url": reverse("resale:offer_edit", args=[rule_id]), "is_active": has_offer}
        for lot_id, rule_id, has_offer in rules.values_list("lot_id", "pk", "has_offer")
    }


@never_cache
@business_login_required
@require_GET
def marketplace_home(request: HttpRequest) -> HttpResponse:
    if not request.business:
        return redirect("businesses:no_business")

    form = MarketplaceItemFilterForm(request.GET or None)
    spec = form.to_spec()
    base = marketplace_lots_for(request.business)
    qs = filter_marketplace_lots(base, spec=spec)
    minimum, maximum = effective_price_bounds(
        base, spec=spec, audience="colleague"
    )
    page = paginate(request, qs)
    resale_choices = _resale_choices(request, page.object_list)
    cards = []
    for lot in page.object_list:
        card = marketplace_lot_card(lot, request.business)
        card["share_url"] = _partner_share_url(request, lot)
        card["retail_price"] = b2c_price_context(lot)
        card["resale_choice"] = resale_choices.get(lot.pk)
        cards.append(card)
    return render(
        request,
        "marketplace/home.html",
        {
            "filter_form": form,
            "cards": cards,
            "page": page,
            "price_bounds": {"minimum": minimum, "maximum": maximum},
        },
    )


@never_cache
@business_login_required
@require_GET
def marketplace_lot_detail(request: HttpRequest, lot_id) -> HttpResponse:
    if not request.business:
        return redirect("businesses:no_business")
    lot = get_marketplace_lot(request.business, lot_id)
    if lot is None:
        messages.error(
            request,
            "این محصول اکنون در بازار قابل مشاهده نیست.",
        )
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
            "retail_price": b2c_price_context(lot),
            "resale_choice": _resale_choices(request, [lot]).get(lot.pk),
            "stock": stock_view(lot),
            "media_items": lot.media.all(),
            "share_url": _partner_share_url(request, lot),
        },
    )


@business_login_required
@require_GET
def marketplace_shared_item(
    request: HttpRequest, public_token: str
) -> HttpResponse:
    """Resolve one opaque B2B link according to the signed-in business.

    A seller lands on their own inventory record; another eligible colleague
    lands on the B2B detail page. Anonymous visitors never reach this view.
    """
    if not request.business:
        return redirect("businesses:no_business")

    own_lot = (
        lots_for_business(request.business)
        .filter(public_token=public_token)
        .first()
    )
    if own_lot is not None:
        return redirect("inventory:lot_detail", lot_id=own_lot.id)

    lot = get_marketplace_lot_by_token(request.business, public_token)
    if lot is None:
        messages.error(
            request,
            "این لینک دیگر محصول منتشرشده‌ای را نشان نمی‌دهد.",
        )
        return redirect("marketplace:home")
    return redirect("marketplace:lot_detail", lot_id=lot.id)


@business_login_required
@require_GET
def archived_inquiry_detail(
    request: HttpRequest, inquiry_id
) -> HttpResponse:
    """Read-only compatibility page for partner inquiries created in the past."""
    inquiry = (
        PartnerInquiry.objects.filter(pk=inquiry_id)
        .filter(
            models.Q(buyer_business=request.business)
            | models.Q(seller_business=request.business)
        )
        .select_related(
            "buyer_business",
            "seller_business",
            "converted_invoice",
        )
        .prefetch_related("items")
        .first()
    )
    if inquiry is None:
        messages.error(request, "سابقه موردنظر یافت نشد.")
        return redirect("marketplace:home")
    return render(
        request,
        "marketplace/inquiry_detail.html",
        {"inquiry": inquiry},
    )
