from __future__ import annotations

import logging

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from apps.businesses.decorators import business_login_required, require_capability
from apps.businesses.permissions import (
    INVENTORY_CONFIRM,
    INVENTORY_CREATE,
    INVENTORY_EDIT,
    INVENTORY_PUBLISH,
    INVENTORY_VIEW,
    PRICES_EDIT,
    PRICES_VIEW,
)
from apps.pricing.models import LotPrice
from apps.pricing.services import resolve_visible_prices

from .forms import (
    ItemDetailsForm,
    ItemEditForm,
    ItemMediaForm,
    ItemStockForm,
    ItemVisibilityForm,
    OwnerItemFilterForm,
    ProductPickForm,
    TierPriceForm,
)
from .freshness import stock_view
from .models import Product
from .selectors import filter_owned_lots, get_business_lot, lots_for_business, products_for_business
from .services import (
    InventoryError,
    add_lot_media,
    confirm_item_stock,
    create_draft_item,
    create_or_get_product,
    delete_item,
    delete_lot_media,
    duplicate_item,
    item_has_commercial_history,
    publish_item,
    reorder_lot_media,
    set_item_availability,
    set_item_visibility,
    set_primary_media,
    update_item,
)

logger = logging.getLogger(__name__)

WIZARD_SESSION_KEY = "inventory_quick_add"
WIZARD_STEPS = 4


def _wizard_data(request: HttpRequest) -> dict:
    return request.session.get(WIZARD_SESSION_KEY, {})


def _save_wizard(request: HttpRequest, data: dict) -> None:
    request.session[WIZARD_SESSION_KEY] = data
    request.session.modified = True


def _clear_wizard(request: HttpRequest) -> None:
    request.session.pop(WIZARD_SESSION_KEY, None)


def _price_spec(form: TierPriceForm) -> dict:
    return {
        "mode": form.cleaned_data["mode"],
        "amount": form.cleaned_data.get("amount"),
        "valid_for_days": form.cleaned_data.get("valid_for_days"),
        "special_amount": form.cleaned_data.get("special_amount"),
        "special_until": form.cleaned_data.get("special_until"),
    }


def _price_initial(lot, tier_code: str) -> dict:
    price = next((p for p in lot.prices.all() if p.tier.code == tier_code), None)
    if price is None:
        return {}
    return {
        "mode": price.mode,
        "amount": price.amount,
        "valid_for_days": price.price_valid_for_days,
        "special_amount": price.special_amount,
        "special_until": price.special_until,
    }


# --- listing and detail -------------------------------------------------------


@business_login_required
@require_capability(INVENTORY_VIEW)
def lot_list(request: HttpRequest) -> HttpResponse:
    form = OwnerItemFilterForm(request.GET or None)
    qs = filter_owned_lots(
        lots_for_business(request.business),
        spec=form.to_spec(),
        state=form.state_value,
    )
    can_view_prices = request.membership.has_capability(PRICES_VIEW)
    rows = []
    for lot in qs[:100]:
        prices = resolve_visible_prices(lot, "owner_staff", can_view_prices=can_view_prices)
        primary = next((m for m in lot.media.all() if m.is_primary), None) or next(iter(lot.media.all()), None)
        rows.append({"lot": lot, "stock": stock_view(lot), "prices": prices, "primary_media": primary})
    return render(
        request,
        "inventory/lot_list.html",
        {"filter_form": form, "rows": rows, "can_view_prices": can_view_prices},
    )


@business_login_required
@require_capability(INVENTORY_VIEW)
def lot_detail(request: HttpRequest, lot_id) -> HttpResponse:
    lot = get_business_lot(request.business, lot_id)
    if lot is None:
        messages.error(request, "محصول یافت نشد.")
        return redirect("inventory:lot_list")
    can_view_prices = request.membership.has_capability(PRICES_VIEW)
    share_url = request.build_absolute_uri(f"/p/{lot.public_token}/")
    return render(
        request,
        "inventory/lot_detail.html",
        {
            "lot": lot,
            "stock": stock_view(lot),
            "prices": resolve_visible_prices(lot, "owner_staff", can_view_prices=can_view_prices),
            "media_items": lot.media.all(),
            "can_view_prices": can_view_prices,
            "can_edit_prices": request.membership.has_capability(PRICES_EDIT),
            "share_url": share_url,
            "has_history": item_has_commercial_history(lot),
        },
    )


@business_login_required
@require_capability(INVENTORY_EDIT)
@require_http_methods(["GET", "POST"])
def lot_edit(request: HttpRequest, lot_id) -> HttpResponse:
    lot = get_business_lot(request.business, lot_id)
    if lot is None:
        messages.error(request, "محصول یافت نشد.")
        return redirect("inventory:lot_list")

    can_edit_prices = request.membership.has_capability(PRICES_EDIT)
    form = ItemEditForm(request.POST or None, instance=lot)
    b2b_form = TierPriceForm(
        request.POST or None, prefix="b2b", tier_label="قیمت همکار", initial=_price_initial(lot, "b2b")
    )
    b2c_form = TierPriceForm(
        request.POST or None, prefix="b2c", tier_label="قیمت مشتری", initial=_price_initial(lot, "b2c")
    )

    if request.method == "POST":
        price_forms_ok = not can_edit_prices or (b2b_form.is_valid() and b2c_form.is_valid())
        if form.is_valid() and price_forms_ok:
            try:
                # One transaction for the whole edit: a rejected price must not
                # leave the other fields already saved.
                update_item(
                    lot=lot,
                    membership=request.membership,
                    fields=form.cleaned_data,
                    b2b_price=_price_spec(b2b_form) if can_edit_prices else None,
                    b2c_price=_price_spec(b2c_form) if can_edit_prices else None,
                )
            except InventoryError as exc:
                messages.error(request, exc.message)
            else:
                messages.success(request, "محصول به‌روزرسانی شد.")
                return redirect("inventory:lot_detail", lot_id=lot.id)

    return render(
        request,
        "inventory/lot_edit.html",
        {
            "lot": lot,
            "form": form,
            "b2b_form": b2b_form,
            "b2c_form": b2c_form,
            "can_edit_prices": can_edit_prices,
        },
    )


# --- lifecycle actions --------------------------------------------------------


@business_login_required
@require_capability(INVENTORY_CONFIRM)
@require_http_methods(["GET", "POST"])
def lot_confirm_stock(request: HttpRequest, lot_id) -> HttpResponse:
    lot = get_business_lot(request.business, lot_id)
    if lot is None:
        messages.error(request, "محصول یافت نشد.")
        return redirect("inventory:lot_list")

    form = ItemStockForm(
        request.POST or None,
        initial={
            "stock_mode": lot.stock_mode,
            "available_sqm": lot.available_sqm,
            "stock_valid_for_days": lot.stock_valid_for_days,
        },
    )
    if request.method == "POST" and form.is_valid():
        try:
            confirm_item_stock(
                lot=lot,
                membership=request.membership,
                stock_mode=form.cleaned_data["stock_mode"],
                available_sqm=form.cleaned_data.get("available_sqm"),
            )
        except InventoryError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(request, "موجودی تأیید شد.")
            return redirect("inventory:lot_detail", lot_id=lot.id)

    return render(request, "inventory/lot_confirm_stock.html", {"lot": lot, "form": form})


@business_login_required
@require_capability(INVENTORY_EDIT)
@require_POST
def lot_set_availability(request: HttpRequest, lot_id) -> HttpResponse:
    lot = get_business_lot(request.business, lot_id)
    if lot is None:
        messages.error(request, "محصول یافت نشد.")
        return redirect("inventory:lot_list")
    available = request.POST.get("available") == "1"
    try:
        set_item_availability(lot=lot, membership=request.membership, available=available)
    except InventoryError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "محصول موجود شد." if available else "محصول ناموجود شد.")
    return redirect("inventory:lot_detail", lot_id=lot.id)


@business_login_required
@require_capability(INVENTORY_PUBLISH)
@require_POST
def lot_set_visibility(request: HttpRequest, lot_id) -> HttpResponse:
    lot = get_business_lot(request.business, lot_id)
    if lot is None:
        messages.error(request, "محصول یافت نشد.")
        return redirect("inventory:lot_list")
    visible = request.POST.get("visible") == "1"
    try:
        set_item_visibility(lot=lot, membership=request.membership, is_visible=visible)
    except InventoryError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "محصول منتشر شد." if visible else "انتشار محصول متوقف شد.")
    return redirect("inventory:lot_detail", lot_id=lot.id)


@business_login_required
@require_capability(INVENTORY_EDIT)
@require_http_methods(["GET", "POST"])
def lot_delete(request: HttpRequest, lot_id) -> HttpResponse:
    lot = get_business_lot(request.business, lot_id)
    if lot is None:
        messages.error(request, "محصول یافت نشد.")
        return redirect("inventory:lot_list")

    if request.method == "POST":
        try:
            delete_item(lot=lot, membership=request.membership)
        except InventoryError as exc:
            messages.error(request, exc.message)
            return redirect("inventory:lot_detail", lot_id=lot.id)
        messages.success(request, "محصول حذف شد.")
        return redirect("inventory:lot_list")

    return render(
        request,
        "inventory/lot_confirm_delete.html",
        {"lot": lot, "has_history": item_has_commercial_history(lot)},
    )


@business_login_required
@require_capability(INVENTORY_CREATE)
@require_POST
def lot_duplicate(request: HttpRequest, lot_id) -> HttpResponse:
    lot = get_business_lot(request.business, lot_id)
    if lot is None:
        messages.error(request, "محصول یافت نشد.")
        return redirect("inventory:lot_list")
    try:
        clone = duplicate_item(lot=lot, membership=request.membership)
    except InventoryError as exc:
        messages.error(request, exc.message)
        return redirect("inventory:lot_detail", lot_id=lot.id)
    messages.success(request, f"کپی ایجاد شد: {clone.lot_code}")
    return redirect("inventory:lot_edit", lot_id=clone.id)


# --- media management ---------------------------------------------------------


@business_login_required
@require_capability(INVENTORY_EDIT)
@require_http_methods(["GET", "POST"])
def lot_media(request: HttpRequest, lot_id) -> HttpResponse:
    lot = get_business_lot(request.business, lot_id)
    if lot is None:
        messages.error(request, "محصول یافت نشد.")
        return redirect("inventory:lot_list")

    form = ItemMediaForm(request.POST or None, request.FILES or None)
    if request.method == "POST":
        action = request.POST.get("action", "upload")
        try:
            if action == "delete":
                delete_lot_media(lot=lot, membership=request.membership, media_id=request.POST.get("media_id"))
                messages.success(request, "فایل حذف شد.")
            elif action == "primary":
                set_primary_media(lot=lot, membership=request.membership, media_id=request.POST.get("media_id"))
                messages.success(request, "تصویر اصلی تغییر کرد.")
            elif action == "reorder":
                reorder_lot_media(
                    lot=lot,
                    membership=request.membership,
                    media_ids=request.POST.getlist("order"),
                )
                messages.success(request, "ترتیب رسانه‌ها ذخیره شد.")
            elif form.is_valid() and request.FILES.get("images"):
                add_lot_media(
                    lot=lot,
                    membership=request.membership,
                    upload=request.FILES["images"],
                    is_primary=form.cleaned_data.get("is_primary", False),
                )
                messages.success(request, "رسانه اضافه شد.")
        except InventoryError as exc:
            messages.error(request, exc.message)
        else:
            return redirect("inventory:lot_media", lot_id=lot.id)

    return render(
        request,
        "inventory/lot_media.html",
        {"lot": lot, "form": form, "media_items": lot.media.all()},
    )


# --- creation flow (4 steps) --------------------------------------------------


@business_login_required
@require_capability(INVENTORY_CREATE)
def quick_add_start(request: HttpRequest) -> HttpResponse:
    _clear_wizard(request)
    return redirect("inventory:quick_add_product")


@business_login_required
@require_capability(INVENTORY_CREATE)
@require_http_methods(["GET", "POST"])
def quick_add_product(request: HttpRequest) -> HttpResponse:
    form = ProductPickForm(request.POST or None, business=request.business)
    if request.method == "POST" and form.is_valid():
        try:
            product = create_or_get_product(
                business=request.business,
                membership=request.membership,
                product_id=form.cleaned_data["product"].id if form.cleaned_data.get("product") else None,
                commercial_name=form.cleaned_data.get("commercial_name", ""),
                stone_type=form.cleaned_data.get("stone_type", ""),
                primary_color=form.cleaned_data.get("primary_color", ""),
                quarry_region=form.cleaned_data.get("quarry_region", ""),
                applications=list(form.cleaned_data.get("applications") or []),
            )
        except InventoryError as exc:
            form.add_error(None, exc.message)
        else:
            data = _wizard_data(request)
            data["product_id"] = str(product.id)
            _save_wizard(request, data)
            return redirect("inventory:quick_add_details")
    return render(
        request,
        "inventory/wizard/product.html",
        {
            "form": form,
            "step": 1,
            "total_steps": WIZARD_STEPS,
            "products": products_for_business(request.business)[:30],
        },
    )


@business_login_required
@require_capability(INVENTORY_CREATE)
@require_http_methods(["GET", "POST"])
def quick_add_details(request: HttpRequest) -> HttpResponse:
    data = _wizard_data(request)
    if not data.get("product_id"):
        return redirect("inventory:quick_add_product")

    form = ItemDetailsForm(
        request.POST or None,
        initial={
            "location_city": request.business.city,
            "location_province": request.business.province,
        },
    )
    if request.method == "POST" and form.is_valid():
        data.update(
            {
                "lot_code": form.cleaned_data.get("lot_code") or "",
                "grade": form.cleaned_data.get("grade") or "",
                "processing_type": form.cleaned_data.get("processing_type") or "",
                "description": form.cleaned_data.get("description") or "",
                "location_province": form.cleaned_data.get("location_province") or "",
                "location_city": form.cleaned_data.get("location_city") or "",
                "location_address": form.cleaned_data.get("location_address") or "",
                "length_cm": str(form.cleaned_data.get("length_cm") or ""),
                "width_cm": str(form.cleaned_data.get("width_cm") or ""),
                "thickness_mm": str(form.cleaned_data.get("thickness_mm") or ""),
                "slab_count": str(form.cleaned_data.get("slab_count") or ""),
            }
        )
        _save_wizard(request, data)
        return redirect("inventory:quick_add_stock")
    return render(
        request,
        "inventory/wizard/details.html",
        {"form": form, "step": 2, "total_steps": WIZARD_STEPS},
    )


@business_login_required
@require_capability(INVENTORY_CREATE)
@require_http_methods(["GET", "POST"])
def quick_add_stock(request: HttpRequest) -> HttpResponse:
    """Step 3 — stock and both price channels, created in one transaction."""
    data = _wizard_data(request)
    if not data.get("product_id"):
        return redirect("inventory:quick_add_product")

    stock_form = ItemStockForm(request.POST or None)
    b2b_form = TierPriceForm(request.POST or None, prefix="b2b", tier_label="قیمت همکار")
    b2c_form = TierPriceForm(request.POST or None, prefix="b2c", tier_label="قیمت مشتری")

    if request.method == "POST" and stock_form.is_valid() and b2b_form.is_valid() and b2c_form.is_valid():
        product = Product.objects.filter(business=request.business, pk=data["product_id"]).first()
        if product is None:
            messages.error(request, "اطلاعات ناقص است. دوباره شروع کنید.")
            return redirect("inventory:quick_add_start")
        try:
            lot = _create_or_update_draft(request, data, product, stock_form, b2b_form, b2c_form)
        except InventoryError as exc:
            stock_form.add_error(None, exc.message)
        else:
            data["lot_id"] = str(lot.id)
            _save_wizard(request, data)
            return redirect("inventory:quick_add_review")

    return render(
        request,
        "inventory/wizard/stock.html",
        {
            "form": stock_form,
            "b2b_form": b2b_form,
            "b2c_form": b2c_form,
            "step": 3,
            "total_steps": WIZARD_STEPS,
        },
    )


def _decimal_or_none(raw: str | None):
    from decimal import Decimal, InvalidOperation

    if not raw:
        return None
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return None


def _int_or_none(raw: str | None):
    try:
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None


def _create_or_update_draft(request, data, product, stock_form, b2b_form, b2c_form):
    """Create the draft item (or update it if the seller went back a step)."""
    existing = get_business_lot(request.business, data["lot_id"]) if data.get("lot_id") else None
    if existing is not None:
        return update_item(
            lot=existing,
            membership=request.membership,
            fields={
                "stock_mode": stock_form.cleaned_data["stock_mode"],
                "available_sqm": stock_form.cleaned_data.get("available_sqm") or 0,
                "stock_valid_for_days": stock_form.cleaned_data["stock_valid_for_days"],
            },
            b2b_price=_price_spec(b2b_form),
            b2c_price=_price_spec(b2c_form),
        )

    lot = create_draft_item(
        business=request.business,
        membership=request.membership,
        product=product,
        lot_code=data.get("lot_code", ""),
        grade=data.get("grade", ""),
        processing_type=data.get("processing_type", ""),
        description=data.get("description", ""),
        location_province=data.get("location_province", ""),
        location_city=data.get("location_city", ""),
        location_address=data.get("location_address", ""),
        stock_mode=stock_form.cleaned_data["stock_mode"],
        available_sqm=stock_form.cleaned_data.get("available_sqm"),
        stock_valid_for_days=stock_form.cleaned_data["stock_valid_for_days"],
        length_cm=_decimal_or_none(data.get("length_cm")),
        width_cm=_decimal_or_none(data.get("width_cm")),
        thickness_mm=_decimal_or_none(data.get("thickness_mm")),
        slab_count=_int_or_none(data.get("slab_count")),
    )
    update_item(
        lot=lot,
        membership=request.membership,
        b2b_price=_price_spec(b2b_form),
        b2c_price=_price_spec(b2c_form),
    )
    return lot


@business_login_required
@require_capability(INVENTORY_CREATE)
@require_http_methods(["GET", "POST"])
def quick_add_review(request: HttpRequest) -> HttpResponse:
    """Step 4 — media, then publish or keep as a draft."""
    data = _wizard_data(request)
    lot = get_business_lot(request.business, data.get("lot_id")) if data.get("lot_id") else None
    if lot is None:
        return redirect("inventory:quick_add_stock")

    media_form = ItemMediaForm(request.POST or None, request.FILES or None)
    visibility_form = ItemVisibilityForm(request.POST or None, initial={"is_visible": True})

    if request.method == "POST":
        action = request.POST.get("action", "publish")
        try:
            if action == "upload":
                if media_form.is_valid() and request.FILES.get("images"):
                    add_lot_media(
                        lot=lot,
                        membership=request.membership,
                        upload=request.FILES["images"],
                        is_primary=media_form.cleaned_data.get("is_primary", False),
                    )
                    messages.success(request, "رسانه اضافه شد.")
                return redirect("inventory:quick_add_review")

            if visibility_form.is_valid():
                if visibility_form.cleaned_data.get("is_urgent_sale"):
                    update_item(
                        lot=lot,
                        membership=request.membership,
                        fields={"is_urgent_sale": True},
                    )
                publish_item(
                    lot=lot,
                    membership=request.membership,
                    is_visible=action == "publish" and visibility_form.cleaned_data.get("is_visible", True),
                )
                _clear_wizard(request)
                messages.success(
                    request,
                    "محصول منتشر شد." if action == "publish" else "محصول به‌عنوان پیش‌نویس ذخیره شد.",
                )
                return redirect("inventory:lot_detail", lot_id=lot.id)
        except InventoryError as exc:
            messages.error(request, exc.message)

    return render(
        request,
        "inventory/wizard/review.html",
        {
            "lot": lot,
            "stock": stock_view(lot),
            "prices": resolve_visible_prices(lot, "owner_staff"),
            "media_items": lot.media.all(),
            "media_form": media_form,
            "visibility_form": visibility_form,
            "step": 4,
            "total_steps": WIZARD_STEPS,
            "inquiry_mode": LotPrice.Mode.INQUIRY,
        },
    )
