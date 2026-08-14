from __future__ import annotations

import logging

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from apps.businesses.eligibility import public_business_or_none
from apps.core.pagination import paginate
from apps.inventory.forms import ItemFilterForm
from apps.inventory.filters import effective_price_bounds

from . import cart
from .selectors import (
    catalog_notes,
    filter_public_lots,
    get_public_item_by_token,
    get_public_lot,
    get_shareable_catalog,
    public_catalog_lots,
    public_items,
    related_public_lots,
)
from .services import b2c_price_context, public_lot_card, record_catalog_view

logger = logging.getLogger(__name__)

COMPARE_SESSION_KEY = "b2c_compare_lot_ids"


def _business_or_404(slug: str):
    """The one gate every public seller page goes through.

    See :func:`apps.businesses.eligibility.public_business_or_none` for why a
    seller who cannot sell gets a 404 rather than an empty shop.
    """
    business = public_business_or_none(slug)
    if business is None:
        raise Http404("این فروشگاه در دسترس نیست.")
    return business


def _compare_ids(request: HttpRequest) -> list[str]:
    raw = request.session.get(COMPARE_SESSION_KEY, [])
    return [str(x) for x in raw][:4]


@require_http_methods(["GET"])
def public_search(request: HttpRequest) -> HttpResponse:
    """Login-free product discovery across every eligible seller."""
    form = ItemFilterForm(request.GET or None)
    spec = form.to_spec()
    base = public_items()
    qs = filter_public_lots(base, spec=spec)
    minimum, maximum = effective_price_bounds(base, spec=spec, audience="public")
    page = paginate(request, qs)
    cards = [public_lot_card(lot) for lot in page.object_list]
    selected = set(cart.selected_ids(request))
    for card in cards:
        card["is_selected"] = str(card["lot"].id) in selected
    return render(
        request,
        "catalog/public_search.html",
        {
            "filter_form": form,
            "cards": cards,
            "page": page,
            "compare_ids": _compare_ids(request),
            "selection_count": cart.count(request),
            "price_bounds": {"minimum": minimum, "maximum": maximum},
        },
    )


@require_http_methods(["GET"])
def storefront(request: HttpRequest, business_slug: str) -> HttpResponse:
    business = _business_or_404(business_slug)
    form = ItemFilterForm(request.GET or None)
    spec = form.to_spec()
    base = public_catalog_lots(business)
    qs = filter_public_lots(base, spec=spec)
    minimum, maximum = effective_price_bounds(base, spec=spec, audience="public")
    page = paginate(request, qs)
    cards = [public_lot_card(lot) for lot in page.object_list]
    selected = set(cart.selected_ids(request))
    for card in cards:
        card["is_selected"] = str(card["lot"].id) in selected
    return render(
        request,
        "catalog/storefront.html",
        {
            "business": business,
            "filter_form": form,
            "cards": cards,
            "page": page,
            "compare_ids": _compare_ids(request),
            "selection_count": cart.count(request),
            "price_bounds": {"minimum": minimum, "maximum": maximum},
        },
    )


@require_http_methods(["GET"])
def lot_detail(request: HttpRequest, business_slug: str, lot_id) -> HttpResponse:
    business = _business_or_404(business_slug)
    lot = get_public_lot(business, lot_id)
    if lot is None:
        return render(request, "catalog/not_found.html", {"business": business}, status=404)

    from apps.inventory.freshness import stock_view

    return render(
        request,
        "catalog/lot_detail.html",
        {
            "business": business,
            "lot": lot,
            "product": lot.product,
            "price": b2c_price_context(lot),
            "stock": stock_view(lot),
            "media_items": list(lot.media.all()),
            "related_cards": [public_lot_card(item) for item in related_public_lots(lot)],
            "is_selected": cart.contains(request, lot.pk),
            "compare_ids": _compare_ids(request),
            "share_url": request.build_absolute_uri(f"/p/{lot.public_token}/"),
        },
    )


@require_http_methods(["GET"])
def shared_item(request: HttpRequest, public_token: str) -> HttpResponse:
    """The stable per-product share link, `/p/<token>/`.

    B2C-safe by construction: it resolves through the public audience even when
    the visitor happens to be a logged-in colleague, so pasting a share URL into
    a colleague's browser cannot surface a B2B price.
    """
    lot = get_public_item_by_token(public_token)
    if lot is None:
        # Hidden, unavailable and deleted all land here. Distinguishing them
        # would tell a stranger which products a seller has withdrawn.
        return render(request, "catalog/item_unavailable.html", status=404)

    from apps.inventory.freshness import stock_view

    return render(
        request,
        "catalog/shared_item.html",
        {
            "business": lot.business,
            "lot": lot,
            "product": lot.product,
            "price": b2c_price_context(lot),
            "stock": stock_view(lot),
            "media_items": list(lot.media.all()),
            "share_url": request.build_absolute_uri(),
        },
    )


@require_http_methods(["GET"])
def shared_catalog(request: HttpRequest, share_token: str) -> HttpResponse:
    catalog = get_shareable_catalog(share_token)
    if catalog is None:
        return render(request, "catalog/catalog_unavailable.html", status=404)

    record_catalog_view(catalog)

    # resolve_catalog already intersected the selected membership with the public
    # eligibility queryset, so everything here is showable.
    # Re-filtering in the template layer is what let a private item slip through
    # before.
    notes = catalog_notes(catalog)
    selected = set(cart.selected_ids(request))
    page = paginate(request, catalog.resolved_items)
    cards = [
        {
            **public_lot_card(item),
            "note": notes.get(str(item.pk), ""),
            "is_selected": str(item.pk) in selected,
        }
        for item in page.object_list
    ]

    return render(
        request,
        "catalog/shared_catalog.html",
        {
            "catalog": catalog,
            "business": catalog.business,
            "cards": cards,
            "page": page,
            "share_url": request.build_absolute_uri(),
            "selection_count": cart.count(request),
        },
    )


@require_http_methods(["POST"])
def compare_toggle(request: HttpRequest, business_slug: str, lot_id) -> HttpResponse:
    business = _business_or_404(business_slug)
    lot = get_public_lot(business, lot_id)
    if lot is None:
        return redirect("catalog:storefront", business_slug=business_slug)
    ids = _compare_ids(request)
    lid = str(lot.id)
    if lid in ids:
        ids = [x for x in ids if x != lid]
    elif len(ids) < 4:
        ids.append(lid)
    request.session[COMPARE_SESSION_KEY] = ids
    request.session.modified = True
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER")
    # Only follow same-host redirects; anything else could be an open redirect.
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)
    return redirect("catalog:compare", business_slug=business_slug)


@require_http_methods(["GET"])
def compare_view(request: HttpRequest, business_slug: str) -> HttpResponse:
    business = _business_or_404(business_slug)
    ids = _compare_ids(request)
    lots = list(public_catalog_lots(business).filter(id__in=ids))
    order = {lid: idx for idx, lid in enumerate(ids)}
    lots.sort(key=lambda lot: order.get(str(lot.id), 99))
    return render(
        request,
        "catalog/compare.html",
        {"business": business, "cards": [public_lot_card(lot) for lot in lots]},
    )
