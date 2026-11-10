from __future__ import annotations

from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from apps.businesses.decorators import require_capability
from apps.businesses.eligibility import can_sell_q
from apps.businesses.models import Business
from apps.businesses.permissions import INVOICE_CREATE, PRICES_EDIT, PRICES_VIEW
from apps.core.pagination import paginate
from apps.inventory.models import InventoryLot
from apps.invoicing.services import InvoiceError

from .forms import OfferForm, PurchaseForm, WarehousePriceForm
from .models import ResaleGrant, ResaleOffer, ResaleRequest, WholesaleProgram
from .orders import active_quote, make_retail_invoice, make_wholesale_invoice, request_purchase
from .pricing import ResaleError, current_terms, offer_price, price_rows, special_end
from .selectors import offer_queryset, resale_prices_prefetch, retailer_rules
from .services import configure_lot, enable_program, set_grant, set_offer, unpublish_offer


@never_cache
@require_capability(PRICES_VIEW)
@require_safe
def index(request):
    page = paginate(request, offer_queryset().filter(grant__retailer=request.business))
    rows = []
    at = timezone.now()
    for offer in page.object_list:
        terms = current_terms(offer.rule, at=at)
        rows.append({
            "rule": offer.rule, "terms": terms, "offer": offer,
            "price": offer_price(offer, terms, at=at),
        })
    return render(request, "resale/index.html", {
        "rows": rows, "page": page,
        "can_edit": request.membership.has_capability(PRICES_EDIT),
        "public_link": request.build_absolute_uri(
            reverse("catalog:storefront", args=[request.business.storefront_token]),
        ),
    })


@never_cache
@require_capability(PRICES_EDIT)
@require_safe
def warehouse(request):
    program = WholesaleProgram.objects.filter(warehouse=request.business).first()
    lots = InventoryLot.objects.filter(business=request.business, deleted_at__isnull=True).select_related(
        "product__stone", "resale_rule",
    ).order_by("lot_code")
    return render(request, "resale/warehouse.html", {
        "program": program, "page": paginate(request, lots) if program else None,
        "grants": program.grants.select_related("retailer").order_by("retailer__name") if program else (),
    })


@require_capability(PRICES_EDIT)
@require_POST
def enable(request):
    try:
        enable_program(warehouse=request.business, membership=request.membership)
    except ResaleError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "مدیریت فروش از انبار فعال شد. قیمت و همکاران مجاز را تنظیم کنید.")
    return redirect("resale:warehouse")


class GrantForm(forms.Form):
    retailer = forms.ModelChoiceField(
        label="کسب‌وکار همکار", queryset=Business.objects.none(),
        widget=forms.Select(attrs={"class": "field-input"}),
    )

    def __init__(self, *args, warehouse, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["retailer"].queryset = Business.objects.filter(can_sell_q()).exclude(pk=warehouse.pk)


@never_cache
@require_capability(PRICES_EDIT)
@require_http_methods(["GET", "POST"])
def grant(request):
    get_object_or_404(WholesaleProgram, warehouse=request.business)
    form = GrantForm(request.POST if request.method == "POST" else None, warehouse=request.business)
    if request.method == "POST" and form.is_valid():
        try:
            set_grant(warehouse=request.business, membership=request.membership, **form.cleaned_data)
        except ResaleError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "اجازهٔ عرضه برای همکار ثبت شد.")
            return redirect("resale:warehouse")
    return render(request, "resale/form.html", {
        "title": "اجازهٔ عرضه به همکار", "form": form, "back_url": reverse("resale:warehouse"),
        "lead": "همکار مجاز می‌تواند از بازار، محصول انبار را انتخاب کند و با قیمت خودش در ویترین عرضه کند.",
    })


@require_capability(PRICES_EDIT)
@require_POST
def revoke(request, grant_id):
    target = get_object_or_404(ResaleGrant.objects.select_related("retailer"), pk=grant_id,
                               program__warehouse=request.business)
    try:
        set_grant(warehouse=request.business, membership=request.membership, retailer=target.retailer, active=False)
    except ResaleError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "عرضهٔ محصولات انبار برای این همکار متوقف شد.")
    return redirect("resale:warehouse")


@never_cache
@require_capability(PRICES_EDIT)
@require_http_methods(["GET", "POST"])
def warehouse_price(request, lot_id):
    get_object_or_404(WholesaleProgram, warehouse=request.business)
    lot = get_object_or_404(
        InventoryLot.objects.select_related("product__stone", "resale_rule"),
        pk=lot_id, business=request.business, deleted_at__isnull=True,
    )
    prices = price_rows(lot)
    b2b, b2c = prices.get("b2b"), prices.get("b2c")
    at = timezone.now()
    end = special_end(b2b, at)
    paired = special_end(b2c, at) is not None
    rule = getattr(lot, "resale_rule", None)
    initial = {
        "margin_kind": rule.margin_kind if rule else "percent", "margin_value": rule.margin_value if rule else None,
        "wholesale": b2b.amount if b2b else None, "retail": b2c.amount if b2c else None,
        "days": min(p.price_valid_for_days for p in prices.values()) if prices else 7,
        "discount": "paired" if paired else "wholesale" if end else "none",
        "special_wholesale": b2b.special_amount if end else None,
        "special_retail": b2c.special_amount if paired else None,
        "special_days": max(1, (end - at).days) if end else None,
    }
    form = WarehousePriceForm(request.POST if request.method == "POST" else None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            configure_lot(
                lot=lot, membership=request.membership, margin_kind=form.cleaned_data["margin_kind"],
                margin_value=form.cleaned_data["margin_value"], specs=form.price_specs(),
            )
        except ValueError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "هر دو قیمت و حداقل سود ذخیره شد.")
            return redirect("resale:warehouse")
    return render(request, "resale/form.html", {
        "title": f"قیمت انبار · {lot.product.commercial_name}", "form": form,
        "lead": "سود ناخالص برای هر متر مربع است. با ذخیره، اعتبار و مدت فروش ویژه از نو شروع می‌شوند.",
        "back_url": reverse("resale:warehouse"),
    })


@never_cache
@require_capability(PRICES_EDIT)
@require_http_methods(["GET", "POST"])
def offer_edit(request, rule_id):
    rule = get_object_or_404(
        retailer_rules(request.business).select_related("lot__product__stone").prefetch_related(
            resale_prices_prefetch("lot__prices"),
        ), pk=rule_id,
    )
    offer = ResaleOffer.objects.filter(rule=rule, grant__retailer=request.business, grant__is_active=True).first()
    at = timezone.now()
    terms = current_terms(rule, at=at)
    initial = {
        "amount": offer.amount if offer else (terms.floor if terms else None),
        "special_amount": offer.special_amount if offer and offer.special_until and offer.special_until > at
        and terms and terms.wholesale_special_until else None,
    }
    form = OfferForm(request.POST if request.method == "POST" else None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            special = form.cleaned_data["special_amount"]
            special_until = terms.expires_at if special is not None and terms else None
            if special_until and special == initial["special_amount"]:
                special_until = min(special_until, offer.special_until)
            set_offer(rule=rule, retailer=request.business, membership=request.membership,
                      amount=form.cleaned_data["amount"], special_amount=special,
                      special_until=special_until)
        except ValueError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "محصول با قیمت شما در ویترین عرضه شد.")
            return redirect("resale:index")
    return render(request, "resale/form.html", {
        "title": f"قیمت فروش شما · {rule.lot.product.commercial_name}", "form": form,
        "terms": terms, "show_terms": True, "back_url": reverse("resale:index"),
        "product_url": reverse("marketplace:lot_detail", args=[rule.lot_id]),
        "submit_label": "ذخیرهٔ قیمت عرضه" if offer and offer.is_active else "ذخیره و عرضه در ویترین",
        "lead": "قیمت ویژهٔ شما فقط تا پایان اعتبار تخفیف همکاری انبار نمایش داده می‌شود.",
    })


@require_capability(PRICES_EDIT)
@require_POST
def offer_stop(request, offer_id):
    offer = get_object_or_404(ResaleOffer.objects.select_related("rule__lot", "grant"), pk=offer_id,
                              grant__retailer=request.business)
    try:
        unpublish_offer(offer=offer, retailer=request.business, membership=request.membership)
    except ResaleError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "عرضهٔ این محصول متوقف شد.")
    return redirect("resale:index")


@never_cache
@require_capability(PRICES_VIEW)
@require_capability(INVOICE_CREATE)
@require_http_methods(["GET", "POST"])
def purchase(request, order_id):
    order = get_object_or_404(
        ResaleRequest.objects.select_related("inquiry", "wholesale_invoice"),
        pk=order_id, inquiry__business=request.business,
    )
    back_url = reverse("inquiries:detail", args=[order.inquiry_id])
    offer = offer_queryset().filter(pk=order.offer_id, grant__retailer=request.business).first()
    try:
        if offer is None:
            raise ResaleError("این محصول دیگر در ویترین عرضه نمی‌شود.")
        wholesale, retail, expires = active_quote(offer)
    except ResaleError as exc:
        messages.error(request, str(exc))
        return redirect(back_url)
    form = PurchaseForm(request.POST if request.method == "POST" else None,
                        initial={"wholesale": wholesale, "retail": retail})
    if request.method == "POST" and form.is_valid():
        try:
            request_purchase(
                order_id=order.pk, retailer=request.business, membership=request.membership,
                expected_wholesale=form.cleaned_data["wholesale"], expected_retail=form.cleaned_data["retail"],
            )
        except ResaleError as exc:
            # A stale form must show the current rate before a new confirmation.
            messages.error(request, str(exc))
            return redirect("resale:purchase", order_id=order.pk)
        messages.success(request, "درخواست خرید برای انبار ثبت شد.")
        return redirect(back_url)
    return render(request, "resale/form.html", {
        "title": "درخواست خرید از انبار", "form": form, "back_url": back_url,
        "lead": "با ثبت درخواست، فقط کالا، متراژ و نرخ خرید به انبار می‌رسد. این مرحله رزرو موجودی نیست.",
        "purchase": {"wholesale": wholesale, "retail": retail, "quantity": order.quantity, "expires": expires,
                     "gross_profit": (retail - wholesale) * order.quantity},
        "submit_label": "تأیید نرخ و درخواست خرید",
    })


@never_cache
@require_capability(PRICES_VIEW)
@require_capability(INVOICE_CREATE)
@require_safe
def warehouse_requests(request):
    rows = ResaleRequest.objects.filter(warehouse=request.business, requested_at__isnull=False).order_by(
        "-requested_at", "pk",
    ).values("pk", "quantity", "wholesale_amount", "quote_until", "wholesale_invoice_id",
             "inquiry__business__name", "inquiry__items__product_name")
    return render(request, "resale/warehouse_requests.html", {"page": paginate(request, rows)})


@require_capability(PRICES_VIEW)
@require_capability(INVOICE_CREATE)
@require_POST
def wholesale_invoice(request, order_id):
    get_object_or_404(ResaleRequest, pk=order_id, warehouse=request.business, requested_at__isnull=False)
    try:
        invoice = make_wholesale_invoice(order_id=order_id, warehouse=request.business, membership=request.membership)
    except (InvoiceError, ResaleError) as exc:
        messages.error(request, str(exc))
        return redirect("resale:warehouse_requests")
    return redirect("invoicing:detail", invoice_id=invoice.pk)


@require_capability(INVOICE_CREATE)
@require_POST
def retail_invoice(request, order_id):
    order = get_object_or_404(ResaleRequest, pk=order_id, inquiry__business=request.business)
    try:
        invoice = make_retail_invoice(order_id=order_id, retailer=request.business, membership=request.membership)
    except (InvoiceError, ResaleError) as exc:
        messages.error(request, str(exc))
        return redirect("inquiries:detail", inquiry_id=order.inquiry_id)
    return redirect("invoicing:detail", invoice_id=invoice.pk)
