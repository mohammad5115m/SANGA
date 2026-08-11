from __future__ import annotations

import logging

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from apps.businesses.decorators import business_login_required, require_capability
from apps.businesses.permissions import INQUIRIES_RESPOND, INQUIRIES_VIEW
from apps.matching.selectors import visible_matches_for
from apps.matching.services import persist_matches

from .forms import PurchaseOfferForm, PurchaseRequestForm
from .models import PurchaseOffer, PurchaseRequest
from .selectors import (
    get_network_request,
    get_own_request,
    my_offer_for,
    my_purchase_requests,
    network_purchase_requests,
    offers_for_requester,
)
from .services import (
    PurchaseRequestError,
    close_purchase_request,
    create_purchase_request,
    decide_offer,
    submit_private_offer,
)

logger = logging.getLogger(__name__)


@business_login_required
@require_capability(INQUIRIES_VIEW)
def my_list(request: HttpRequest) -> HttpResponse:
    if not request.business:
        return redirect("businesses:onboarding_start")
    items = my_purchase_requests(request.business)
    return render(request, "purchase_requests/my_list.html", {"items": items})


@business_login_required
@require_capability(INQUIRIES_VIEW)
def network_list(request: HttpRequest) -> HttpResponse:
    if not request.business:
        return redirect("businesses:onboarding_start")
    items = network_purchase_requests(request.business)[:80]
    return render(request, "purchase_requests/network_list.html", {"items": items})


@business_login_required
@require_capability(INQUIRIES_RESPOND)
@require_http_methods(["GET", "POST"])
def create(request: HttpRequest) -> HttpResponse:
    if not request.business:
        return redirect("businesses:onboarding_start")
    form = PurchaseRequestForm(request.POST or None, initial={"similar_accepted": True, "is_public_to_network": True})
    if request.method == "POST" and form.is_valid():
        try:
            pr = create_purchase_request(
                business=request.business,
                membership=request.membership,
                **form.cleaned_data,
            )
        except PurchaseRequestError as exc:
            form.add_error(None, exc.message)
        except Exception:
            logger.exception("PR create failed")
            form.add_error(None, "ثبت درخواست با خطا روبه‌رو شد.")
        else:
            messages.success(request, "درخواست خرید ثبت و تطبیق اولیه انجام شد.")
            return redirect("purchase_requests:detail", pr_id=pr.id)
    return render(request, "purchase_requests/form.html", {"form": form})


@business_login_required
@require_capability(INQUIRIES_VIEW)
def detail(request: HttpRequest, pr_id) -> HttpResponse:
    if not request.business:
        return redirect("businesses:onboarding_start")
    pr = get_own_request(request.business, pr_id)
    if pr is None:
        messages.error(request, "درخواست یافت نشد.")
        return redirect("purchase_requests:my_list")
    offers = offers_for_requester(pr)
    # Persisted matches are re-checked against current marketplace visibility, so a
    # revoked partnership hides them immediately instead of at the next rematch.
    matches = visible_matches_for(pr, request.business)[:30]
    return render(
        request,
        "purchase_requests/detail.html",
        {"pr": pr, "offers": offers, "matches": matches},
    )


@business_login_required
@require_capability(INQUIRIES_RESPOND)
@require_POST
def rematch(request: HttpRequest, pr_id) -> HttpResponse:
    pr = get_own_request(request.business, pr_id) if request.business else None
    if pr is None:
        messages.error(request, "درخواست یافت نشد.")
        return redirect("purchase_requests:my_list")
    persist_matches(pr)
    messages.success(request, "تطبیق دوباره اجرا شد.")
    return redirect("purchase_requests:detail", pr_id=pr.id)


@business_login_required
@require_capability(INQUIRIES_RESPOND)
@require_POST
def close(request: HttpRequest, pr_id) -> HttpResponse:
    pr = get_own_request(request.business, pr_id) if request.business else None
    if pr is None:
        messages.error(request, "درخواست یافت نشد.")
        return redirect("purchase_requests:my_list")
    try:
        close_purchase_request(purchase_request=pr, membership=request.membership)
    except PurchaseRequestError as exc:
        messages.error(request, exc.message)
    else:
        messages.info(request, "درخواست لغو شد.")
    return redirect("purchase_requests:my_list")


@business_login_required
@require_capability(INQUIRIES_VIEW)
@require_http_methods(["GET", "POST"])
def network_detail(request: HttpRequest, pr_id) -> HttpResponse:
    if not request.business:
        return redirect("businesses:onboarding_start")
    pr = get_network_request(request.business, pr_id)
    if pr is None:
        messages.error(request, "این درخواست در شبکه قابل مشاهده نیست.")
        return redirect("purchase_requests:network_list")

    # Browsing the demand board only needs inquiries.view; quoting on it needs
    # inquiries.respond, which the service enforces again.
    can_offer = request.membership.has_capability(INQUIRIES_RESPOND)
    my_offer = my_offer_for(pr, request.business)
    if request.method == "POST" and not can_offer:
        messages.error(request, "دسترسی لازم برای ارسال پیشنهاد را ندارید.")
        return redirect("purchase_requests:network_detail", pr_id=pr.id)

    form = PurchaseOfferForm(request.POST or None, business=request.business)
    if request.method == "POST" and form.is_valid():
        try:
            submit_private_offer(
                purchase_request=pr,
                seller_business=request.business,
                membership=request.membership,
                unit_price=form.cleaned_data["unit_price"],
                offered_qty_sqm=form.cleaned_data["offered_qty_sqm"],
                message=form.cleaned_data.get("message", ""),
                lot=form.cleaned_data.get("lot"),
            )
        except PurchaseRequestError as exc:
            form.add_error(None, exc.message)
        else:
            messages.success(request, "پیشنهاد خصوصی ارسال شد. سایر فروشندگان آن را نمی‌بینند.")
            return redirect("purchase_requests:network_detail", pr_id=pr.id)

    # Privacy: never expose other sellers' offers on network detail.
    return render(
        request,
        "purchase_requests/network_detail.html",
        {"pr": pr, "form": form, "my_offer": my_offer, "can_offer": can_offer},
    )


@business_login_required
@require_capability(INQUIRIES_RESPOND)
@require_POST
def offer_decide(request: HttpRequest, offer_id) -> HttpResponse:
    offer = get_object_or_404(
        PurchaseOffer.objects.select_related("purchase_request"),
        pk=offer_id,
        purchase_request__business=request.business,
    )
    accept = request.POST.get("decision") == "accept"
    try:
        decide_offer(offer=offer, membership=request.membership, accept=accept)
    except PurchaseRequestError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "تصمیم ثبت شد.")
    return redirect("purchase_requests:detail", pr_id=offer.purchase_request_id)
