from __future__ import annotations

import logging

from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from apps.businesses.models import Business
from apps.inquiries.models import Inquiry
from apps.inquiries.services import InquiryError, create_inquiry

from .forms import InquiryForm, StorefrontFilterForm
from .selectors import (
    filter_public_lots,
    get_public_lot,
    get_shareable_catalog,
    public_catalog_lots,
    related_public_lots,
)
from .services import b2c_price_context, public_lot_card, record_catalog_view

logger = logging.getLogger(__name__)

COMPARE_SESSION_KEY = "b2c_compare_lot_ids"


def _business_or_404(slug: str) -> Business:
    return get_object_or_404(Business, slug=slug, status=Business.Status.ACTIVE)


def _compare_ids(request: HttpRequest) -> list[str]:
    raw = request.session.get(COMPARE_SESSION_KEY, [])
    return [str(x) for x in raw][:4]


@require_http_methods(["GET"])
def storefront(request: HttpRequest, business_slug: str) -> HttpResponse:
    business = _business_or_404(business_slug)
    form = StorefrontFilterForm(request.GET or None)
    qs = public_catalog_lots(business)
    if form.is_valid():
        qs = filter_public_lots(
            qs,
            q=form.cleaned_data.get("q", ""),
            stone_type=form.cleaned_data.get("stone_type", ""),
            color=form.cleaned_data.get("color", ""),
            only_urgent=bool(form.cleaned_data.get("only_urgent")),
        )
    cards = [public_lot_card(lot) for lot in qs[:60]]
    return render(
        request,
        "catalog/storefront.html",
        {
            "business": business,
            "filter_form": form,
            "cards": cards,
            "compare_ids": _compare_ids(request),
        },
    )


@require_http_methods(["GET", "POST"])
def lot_detail(request: HttpRequest, business_slug: str, lot_id) -> HttpResponse:
    business = _business_or_404(business_slug)
    lot = get_public_lot(business, lot_id)
    if lot is None:
        return render(request, "catalog/not_found.html", {"business": business}, status=404)

    form = InquiryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            create_inquiry(
                business=business,
                lot=lot,
                name=form.cleaned_data["name"],
                phone=form.cleaned_data["phone"],
                message=form.cleaned_data.get("message", ""),
                source=Inquiry.Source.LOT_DETAIL,
                requester=request.user,
            )
        except InquiryError as exc:
            form.add_error(None, exc.message)
        except Exception:
            logger.exception("Public inquiry failed")
            form.add_error(None, "ارسال استعلام با خطا روبه‌رو شد. دوباره تلاش کنید.")
        else:
            return render(
                request,
                "catalog/inquiry_thanks.html",
                {"business": business, "lot": lot},
            )

    related = [public_lot_card(item) for item in related_public_lots(lot)]
    price = b2c_price_context(lot)
    # Hard guarantee for templates/tests: never pass a prices dict that could include b2b.
    return render(
        request,
        "catalog/lot_detail.html",
        {
            "business": business,
            "lot": lot,
            "product": lot.product,
            "price": price,
            "media_items": [m for m in lot.media.all() if m.kind == "image"],
            "related_cards": related,
            "inquiry_form": form,
            "compare_ids": _compare_ids(request),
            "share_url": request.build_absolute_uri(),
        },
    )


@require_http_methods(["GET", "POST"])
def shared_catalog(request: HttpRequest, share_token: str) -> HttpResponse:
    catalog = get_shareable_catalog(share_token)
    if catalog is None:
        return render(request, "catalog/catalog_unavailable.html", status=404)

    if request.method == "GET":
        record_catalog_view(catalog)

    form = InquiryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            create_inquiry(
                business=catalog.business,
                custom_catalog=catalog,
                name=form.cleaned_data["name"],
                phone=form.cleaned_data["phone"],
                message=form.cleaned_data.get("message", ""),
                source=Inquiry.Source.CUSTOM_CATALOG,
                requester=request.user,
            )
        except InquiryError as exc:
            form.add_error(None, exc.message)
        else:
            return render(
                request,
                "catalog/inquiry_thanks.html",
                {"business": catalog.business, "catalog": catalog},
            )

    items = getattr(catalog, "prefetched_items", list(catalog.items.select_related("lot__product")))
    cards = []
    for item in items:
        lot = item.lot
        # Curated share links may include owner-selected lots; never show unavailable/sold/hidden.
        if lot.archived_at is not None or lot.status in {
            lot.Status.HIDDEN,
            lot.Status.DRAFT,
            lot.Status.SOLD,
            lot.Status.EXPIRED,
        }:
            continue
        cards.append({**public_lot_card(lot), "note": item.note})

    return render(
        request,
        "catalog/shared_catalog.html",
        {
            "catalog": catalog,
            "business": catalog.business,
            "cards": cards,
            "inquiry_form": form,
            "share_url": request.build_absolute_uri(),
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
    # Preserve session order
    order = {lid: idx for idx, lid in enumerate(ids)}
    lots.sort(key=lambda lot: order.get(str(lot.id), 99))
    cards = [public_lot_card(lot) for lot in lots]
    return render(
        request,
        "catalog/compare.html",
        {"business": business, "cards": cards},
    )
