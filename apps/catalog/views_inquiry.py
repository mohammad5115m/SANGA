"""The public customer inquiry flow — the only one there is.

    browse → select → quantity → identity → OTP → submit → one inquiry per seller

Every public entry point funnels through here. The product detail page and the
shared catalog used to carry their own name/phone forms that called
``create_inquiry`` directly, so the most obvious button on the most visited page
recorded an inquiry with an unverified phone, no quantity and often no product
rows at all — while the designed flow next to it asked for all three. Two
workflows for one intention, disagreeing about what an inquiry even contains.

Identity is still asked for once, at the end. Nothing interrupts browsing.
"""

from __future__ import annotations

import logging
import uuid

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.services import OTPError, request_customer_otp, verify_customer_otp
from apps.inquiries.models import Inquiry
from apps.inquiries.services import InquiryError, submit_public_inquiry, validate_phone
from apps.inventory.policy import get_eligible_item

from . import cart
from .forms import CustomerIdentityForm, OTPCodeForm

logger = logging.getLogger(__name__)

PENDING_KEY = "public_inquiry_pending"
DONE_KEY = "public_inquiry_done"

STOCK_QUESTION = "درخواست استعلام موجودی"


def _safe_next(request: HttpRequest, fallback: str) -> str:
    candidate = request.POST.get("next") or request.META.get("HTTP_REFERER")
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return fallback


# --- entering the flow --------------------------------------------------------


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


@require_POST
def inquiry_start(request: HttpRequest, item_id) -> HttpResponse:
    """«درخواست استعلام» from a product page — select it, then continue.

    The same three steps as any other selection. This exists so the product page
    has a one-click way in without also having a second, weaker inquiry form.
    """
    item = get_eligible_item(audience="public", item_id=item_id)
    if item is None:
        return render(request, "catalog/item_unavailable.html", status=404)

    cart.add(request, item)
    cart.set_source(request, Inquiry.Source.ITEM_DETAIL)
    return redirect("catalog:inquiry_review")


@require_POST
def stock_inquiry(request: HttpRequest, item_id) -> HttpResponse:
    """«استعلام موجودی» — ask whether a stale quantity still holds.

    The same pipeline as any other inquiry, seeded with the question, so the
    phone behind it is verified like every other. It lands in the same inbox; the
    seller's reply is either confirming the stock or marking the product ناموجود.
    """
    item = get_eligible_item(audience="public", item_id=item_id)
    if item is None:
        return render(request, "catalog/item_unavailable.html", status=404)

    cart.add(request, item)
    cart.set_source(request, Inquiry.Source.ITEM_DETAIL)
    cart.set_message_seed(request, STOCK_QUESTION)
    return redirect("catalog:inquiry_identify")


# --- the flow -----------------------------------------------------------------


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

    form = CustomerIdentityForm(
        request.POST or None,
        initial={"message": cart.message_seed(request)},
    )
    if request.method == "POST" and form.is_valid():
        try:
            phone = validate_phone(form.cleaned_data["phone"])
            result = request_customer_otp(phone, request=request)
        except (InquiryError, OTPError) as exc:
            form.add_error(None, exc.message)
        else:
            # Minted here, before the code is sent, so every later attempt at
            # this submission — a refresh, a double-click, a retry after a
            # failure — carries the same token and resolves to the same rows.
            request.session[PENDING_KEY] = {
                "submission_id": str(uuid.uuid4()),
                "name": form.cleaned_data["name"],
                "phone": phone,
                "message": form.cleaned_data.get("message", "") or cart.message_seed(request),
                "source": cart.source(request),
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
                return _submit(request, pending, rows)

    return render(
        request,
        "catalog/inquiry_verify.html",
        {"form": form, "phone": pending["phone"], "rows": rows},
    )


def _submit(request: HttpRequest, pending: dict, rows: list[dict]) -> HttpResponse:
    """Persist one inquiry per seller, then show the thank-you page.

    Saving happens here and share buttons appear on the next page — never the
    other way round. A seller must not depend on a WhatsApp message the customer
    may never send.

    The whole submission is one transaction keyed by the token minted before the
    OTP was sent, so a failure part-way through leaves nothing behind and the
    retry that follows is handed the same inquiries rather than a second set.
    """
    try:
        created = submit_public_inquiry(
            submission_id=pending["submission_id"],
            groups=cart.group_by_seller(rows),
            name=pending["name"],
            phone=pending["phone"],
            message=pending.get("message", ""),
            source=pending.get("source") or Inquiry.Source.PUBLIC_SEARCH,
            requester=request.user,
            verified=True,
        )
    except InquiryError as exc:
        messages.error(request, exc.message)
        return redirect("catalog:inquiry_review")
    except Exception:
        logger.exception("Public inquiry submission failed")
        messages.error(request, "ثبت درخواست با خطا روبه‌رو شد. دوباره تلاش کنید.")
        return redirect("catalog:inquiry_review")

    cart.clear(request)
    request.session.pop(PENDING_KEY, None)
    request.session[DONE_KEY] = [str(inquiry.id) for inquiry in created]
    request.session.modified = True
    return redirect("catalog:inquiry_done")


@require_http_methods(["GET"])
def inquiry_done(request: HttpRequest) -> HttpResponse:
    ids = request.session.get(DONE_KEY) or []
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
