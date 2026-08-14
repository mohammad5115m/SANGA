from __future__ import annotations

import logging

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from apps.businesses.decorators import business_login_required
from apps.core.pagination import paginate
from apps.inventory.forms import ItemFilterForm
from apps.inventory.filters import effective_price_bounds

from .selectors import filter_marketplace_lots, get_marketplace_lot, marketplace_lots_for
from .services import b2b_price_context, marketplace_lot_card

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
        },
    )
