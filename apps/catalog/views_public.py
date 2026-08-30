from __future__ import annotations

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from apps.businesses.eligibility import public_business_by_storefront_token_or_none
from apps.core.pagination import paginate
from apps.inventory.filters import effective_price_bounds
from apps.inventory.forms import ItemFilterForm

from . import cart
from .selectors import (
    active_special_lots,
    catalog_notes,
    filter_public_lots,
    get_public_item_by_token,
    get_public_lot,
    get_shareable_catalog,
    public_catalog_lots,
    related_public_lots,
    storefront_collection_sections,
)
from .services import b2c_price_context, public_lot_card, record_catalog_view


def _business_or_404(token: str):
    """The one gate every public seller page goes through.

    See :func:`apps.businesses.eligibility.public_business_by_storefront_token_or_none` for why a
    seller who cannot sell gets a 404 rather than an empty shop.
    """
    business = public_business_by_storefront_token_or_none(token)
    if business is None:
        raise Http404("این فروشگاه در دسترس نیست.")
    return business


@require_http_methods(["GET"])
def storefront(request: HttpRequest, storefront_token: str) -> HttpResponse:
    business = _business_or_404(storefront_token)
    form = ItemFilterForm(request.GET or None)
    spec = form.to_spec()
    base = public_catalog_lots(business)
    qs = filter_public_lots(base, spec=spec)
    minimum, maximum = effective_price_bounds(base, spec=spec, audience="public")
    page = paginate(request, qs)
    cards = [public_lot_card(lot) for lot in page.object_list]
    selected = set(cart.selected_ids(request, business, catalog=None))
    for card in cards:
        card["is_selected"] = str(card["lot"].id) in selected
    special_cards = [public_lot_card(lot) for lot in active_special_lots(business)]
    for card in special_cards:
        card["is_selected"] = str(card["lot"].id) in selected
    collection_sections = []
    for collection in storefront_collection_sections(business):
        collection_cards = [public_lot_card(item.lot) for item in collection.public_items]
        for card in collection_cards:
            card["is_selected"] = str(card["lot"].id) in selected
        if collection_cards:
            collection_sections.append({"collection": collection, "cards": collection_cards})
    return render(
        request,
        "catalog/storefront.html",
        {
            "business": business,
            "filter_form": form,
            "cards": cards,
            "special_cards": special_cards,
            "collection_sections": collection_sections,
            "page": page,
            "selection_count": cart.count(request, business, catalog=None),
            "price_bounds": {"minimum": minimum, "maximum": maximum},
        },
    )


@require_http_methods(["GET"])
def lot_detail(request: HttpRequest, storefront_token: str, lot_id) -> HttpResponse:
    business = _business_or_404(storefront_token)
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
            "is_selected": cart.contains(request, business, lot.pk),
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
            "storefront_token": lot.business.storefront_token,
        },
    )


@require_http_methods(["GET"])
def shared_catalog(request: HttpRequest, share_token: str) -> HttpResponse:
    catalog = get_shareable_catalog(share_token)
    if catalog is None:
        return render(request, "catalog/catalog_unavailable.html", status=404)

    view_cookie = f"catalog_view_{catalog.pk.hex}"
    is_new_open = request.COOKIES.get(view_cookie) != "1"
    if is_new_open:
        record_catalog_view(catalog)

    # resolve_catalog already intersected the selected membership with the public
    # eligibility queryset, so everything here is showable.
    # Re-filtering in the template layer is what let a private item slip through
    # before.
    notes = catalog_notes(catalog)
    selected = set(cart.selected_ids(request, catalog.business, catalog=catalog))
    page = paginate(request, catalog.resolved_items)
    cards = [
        {
            **public_lot_card(item),
            "note": notes.get(str(item.pk), ""),
            "is_selected": str(item.pk) in selected,
        }
        for item in page.object_list
    ]

    response = render(
        request,
        "catalog/shared_catalog.html",
        {
            "catalog": catalog,
            "business": catalog.business,
            "cards": cards,
            "page": page,
            "share_url": request.build_absolute_uri(),
            "selection_count": cart.count(request, catalog.business, catalog=catalog),
            "storefront_token": catalog.business.storefront_token,
        },
    )
    if is_new_open:
        response.set_cookie(
            view_cookie,
            "1",
            max_age=1800,
            httponly=True,
            secure=request.is_secure(),
            samesite="Lax",
        )
    return response
