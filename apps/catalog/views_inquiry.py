"""The public multi-product inquiry flow.

Browse → select → review → identify → submit. Identity is asked for once, at the
end. Nothing interrupts browsing to capture a lead.
"""

from __future__ import annotations

import logging

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.services import OTPError, request_customer_otp, verify_customer_otp
from apps.inquiries.models import Inquiry
from apps.inquiries.services import (
    InquiryError,
    create_inquiry,
    create_stock_inquiry,
    validate_phone,
)
from apps.inventory.policy import get_eligible_item

from . import cart
from .forms import CustomerIdentityForm, OTPCodeForm

logger = logging.getLogger(__name__)

PENDING_KEY = "public_inquiry_pending"


def _safe_next(request: HttpRequest, fallback: str) -> str:
    candidate = request.POST.get("next") or request.META.get("HTTP_REFERER")
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return fallback


@require_http_methods(["GET", "POST"])
def stock_inquiry(request: HttpRequest, item_id) -> HttpResponse:
    """«استعلام موجودی» — ask whether a stale quantity still holds.

    Recorded as a normal inquiry so it lands in the same inbox. The seller's
    reply is either confirming the stock or marking the product ناموجود.
    """
    item = get_eligible_item(audience="public", item_id=item_id)
    if item is None:
        return render(request, "catalog/item_unavailable.html", status=404)

    form = CustomerIdentityForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            create_stock_inquiry(
                item=item,
                name=form.cleaned_data["name"],
                phone=form.cleaned_data["phone"],
                message=form.cleaned_data.get("message", ""),
                requester=request.user,
            )
        except InquiryError as exc:
            form.add_error(None, exc.message)
        else:
            messages.success(request, "درخواست استعلام موجودی برای فروشنده ارسال شد.")
            return render(
                request,
                "catalog/inquiry_thanks.html",
                {"business": item.business, "lot": item},
            )

    return render(request, "catalog/stock_inquiry.html", {"item": item, "form": form})


@require_POST
def selection_toggle(request: HttpRequest, item_id) -> HttpResponse:
    item = get_eligible_item(audience="public", item_id=item_id)
    if item is not None:
        cart.toggle(request, item)
    return redirect(_safe_next(request, "/search/"))


@require_POST
def selection_remove(request: HttpRequest, item_id) -> HttpResponse:
    cart.remove(request, item_id)
    return redirect(_safe_next(request, "/inquiry/"))


@require_http_methods(["GET", "POST"])
def selection_review(request: HttpRequest) -> HttpResponse:
    """Review the selection, set a quantity per product, then identify yourself."""
    rows = cart.resolve(request)

    if request.method == "POST" and rows:
        for row in rows:
            cart.set_quantity(request, row["item"].pk, request.POST.get(f"qty-{row['item'].pk}", ""))
        return redirect("catalog:inquiry_identify")

    return render(
        request,
        "catalog/inquiry_review.html",
        {"rows": rows, "groups": cart.group_by_seller(rows)},
    )


@require_http_methods(["GET", "POST"])
def inquiry_identify(request: HttpRequest) -> HttpResponse:
    """Ask who they are, and send a verification code."""
    rows = cart.resolve(request)
    if not rows:
        messages.info(request, "هنوز محصولی انتخاب نکرده‌اید.")
        return redirect("catalog:public_search")

    form = CustomerIdentityForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            phone = validate_phone(form.cleaned_data["phone"])
            result = request_customer_otp(phone, request=request)
        except (InquiryError, OTPError) as exc:
            form.add_error(None, exc.message)
        else:
            request.session[PENDING_KEY] = {
                "name": form.cleaned_data["name"],
                "phone": phone,
                "message": form.cleaned_data.get("message", ""),
            }
            request.session.modified = True
            if result.dev_code:
                messages.info(request, f"کد توسعه (فقط DEBUG): {result.dev_code}")
            return redirect("catalog:inquiry_verify")

    return render(
        request,
        "catalog/inquiry_identify.html",
        {"form": form, "rows": rows},
    )


@require_http_methods(["GET", "POST"])
def inquiry_verify(request: HttpRequest) -> HttpResponse:
    """Confirm the code, then save.

    Verification creates no User and no session. It only records that the phone
    was reachable, on the CustomerLead.
    """
    pending = request.session.get(PENDING_KEY)
    rows = cart.resolve(request)
    if not pending or not rows:
        return redirect("catalog:public_search")

    form = OTPCodeForm(request.POST or None)
    if request.method == "POST":
        if request.POST.get("action") == "resend":
            try:
                result = request_customer_otp(pending["phone"], request=request)
            except OTPError as exc:
                messages.error(request, exc.message)
            else:
                if result.dev_code:
                    messages.info(request, f"کد توسعه (فقط DEBUG): {result.dev_code}")
            return redirect("catalog:inquiry_verify")

        if form.is_valid():
            try:
                verify_customer_otp(pending["phone"], form.cleaned_data["code"])
            except OTPError as exc:
                form.add_error("code", exc.message)
            else:
                return _submit(request, pending, rows, verified=True)

    return render(
        request,
        "catalog/inquiry_verify.html",
        {"form": form, "phone": pending["phone"], "rows": rows},
    )


def _submit(request: HttpRequest, pending: dict, rows: list[dict], *, verified: bool) -> HttpResponse:
    """Persist one inquiry per seller, then show the thank-you page.

    Saving happens here and share buttons appear on the next page — never the
    other way round. A seller must not depend on a WhatsApp message the customer
    may never send.
    """
    created: list[Inquiry] = []
    try:
        for group in cart.group_by_seller(rows):
            inquiry = create_inquiry(
                business=group["business"],
                name=pending["name"],
                phone=pending["phone"],
                message=pending.get("message", ""),
                items=group["rows"],
                source=Inquiry.Source.PUBLIC_SEARCH,
                requester=request.user,
                verified=verified,
            )
            created.append(inquiry)
    except InquiryError as exc:
        messages.error(request, exc.message)
        return redirect("catalog:inquiry_review")
    except Exception:
        logger.exception("Public inquiry submission failed")
        messages.error(request, "ثبت درخواست با خطا روبه‌رو شد. دوباره تلاش کنید.")
        return redirect("catalog:inquiry_review")

    cart.clear(request)
    request.session.pop(PENDING_KEY, None)
    request.session["public_inquiry_done"] = [str(inquiry.id) for inquiry in created]
    request.session.modified = True
    return redirect("catalog:inquiry_done")


@require_http_methods(["GET"])
def inquiry_done(request: HttpRequest) -> HttpResponse:
    ids = request.session.get("public_inquiry_done") or []
    inquiries = (
        Inquiry.objects.filter(id__in=ids).select_related("business").prefetch_related("items")
        if ids
        else []
    )
    return render(
        request,
        "catalog/inquiry_done.html",
        {"inquiries": inquiries, "share_url": request.build_absolute_uri("/search/")},
    )
