from __future__ import annotations

import time
import uuid

from django.conf import settings
from django.contrib import messages
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_safe

from apps.accounts.password_auth import claim_attempt
from apps.accounts.services import OTPError, request_customer_otp, verify_customer_otp
from apps.businesses.eligibility import public_business_by_storefront_token_or_none
from apps.catalog.forms import OTPCodeForm
from apps.core.pagination import paginate
from apps.core.persian import normalize_persian_text
from apps.inquiries.services import InquiryError, validate_phone
from apps.inventory.freshness import stock_view

from .forms import ResaleInquiryForm
from .orders import submit_request
from .pricing import ResaleError, current_terms, offer_price
from .selectors import offer_queryset

PENDING = "resale_inquiry_pending"


def _retailer(token):
    business = public_business_by_storefront_token_or_none(token)
    if business is None:
        raise Http404("این ویترین در دسترس نیست.")
    return business


def _card(offer):
    """Only the retail amount enters the public render context."""
    lot = offer.rule.lot
    media = [item for item in lot.media.all() if item.kind == "image"]
    image = next((item for item in media if item.is_primary), media[0] if media else None)
    display = offer_price(offer, current_terms(offer.rule))
    return {
        "id": offer.pk, "name": lot.product.commercial_name, "stone": lot.product.stone.name,
        "amount": display.amount, "is_special": display.special,
        "image_url": image.file.url if image and image.file else "",
        "stock": stock_view(lot).label,
    }


def _offers(retailer):
    return offer_queryset().filter(grant__retailer=retailer)


@never_cache
@require_safe
def public_store(request, token):
    retailer = _retailer(token)
    offers = _offers(retailer)
    term = normalize_persian_text(request.GET.get("q", "")[:100]).strip()
    if term:
        for word in term.split()[:8]:
            offers = offers.filter(
                Q(rule__lot__product__name_suffix__icontains=word)
                | Q(rule__lot__product__stone__name__icontains=word)
            )
    page = paginate(request, offers)
    return render(request, "resale/public_store.html", {
        "business": retailer, "cards": [_card(offer) for offer in page.object_list], "page": page, "term": term,
    })


def _persist(request, retailer, token, data, form, *, verified):
    try:
        submit_request(
            offer_id=data["offer_id"], retailer=retailer, quantity=data["quantity"],
            submission_id=data["submission_id"], name=data["name"], phone=data["phone"],
            message=data["message"], verified=verified,
        )
    except (InquiryError, ResaleError) as exc:
        form.add_error(None, str(exc))
        return None
    request.session.pop(PENDING, None)
    messages.success(request, "درخواست شما برای همین فروشنده ثبت شد. این درخواست به معنی خرید قطعی نیست.")
    return redirect("resale:public_store", token=token)


@sensitive_post_parameters("name", "phone", "message")
@never_cache
@require_http_methods(["GET", "POST"])
def ask(request, token, offer_id):
    retailer = _retailer(token)
    offer = get_object_or_404(_offers(retailer), pk=offer_id)
    form = ResaleInquiryForm(request.POST if request.method == "POST" else None,
                             initial={"submission_id": uuid.uuid4()})
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            phone = validate_phone(data["phone"])
            if settings.CUSTOMER_OTP_ENABLED:
                result = request_customer_otp(phone, request=request)
            else:
                claim_attempt(phone=phone, request=request, purpose="customer-inquiry")
                result = None
        except (InquiryError, OTPError) as exc:
            form.add_error(None, str(exc.message))
        else:
            payload = {
                "offer_id": str(offer.pk), "retailer_id": str(retailer.pk),
                "quantity": str(data["quantity"]), "submission_id": str(data["submission_id"] or uuid.uuid4()),
                "name": data["name"], "phone": phone, "message": data["message"], "sent_at": time.time(),
            }
            if result is not None:
                request.session[PENDING] = payload
                if result.dev_code:
                    messages.info(request, f"کد توسعه (فقط DEBUG): {result.dev_code}")
                return redirect("resale:verify", token=token, offer_id=offer.pk)
            response = _persist(request, retailer, token, payload, form, verified=False)
            if response is not None:
                return response
    return render(request, "resale/public_ask.html", {
        "business": retailer, "card": _card(offer), "form": form, "title": "درخواست خرید",
    })


@sensitive_post_parameters("code")
@never_cache
@require_http_methods(["GET", "POST"])
def verify(request, token, offer_id):
    retailer = _retailer(token)
    offer = get_object_or_404(_offers(retailer), pk=offer_id)
    data = request.session.get(PENDING, {})
    if (
        not settings.CUSTOMER_OTP_ENABLED or data.get("retailer_id") != str(retailer.pk)
        or data.get("offer_id") != str(offer_id) or time.time() - data.get("sent_at", 0) > 600
    ):
        return redirect("resale:ask", token=token, offer_id=offer_id)
    form = OTPCodeForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            verify_customer_otp(data["phone"], form.cleaned_data["code"])
        except OTPError as exc:
            form.add_error("code", exc.message)
        else:
            response = _persist(request, retailer, token, data, form, verified=True)
            if response is not None:
                return response
    return render(request, "resale/public_ask.html", {
        "business": retailer, "card": _card(offer), "form": form, "title": "تأیید شمارهٔ تماس",
    })
