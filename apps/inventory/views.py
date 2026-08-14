from __future__ import annotations

import logging
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpRequest, HttpResponse, QueryDict
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from apps.businesses.decorators import business_login_required, require_capability
from apps.businesses.permissions import (
    CATALOG_MANAGE,
    INVENTORY_CONFIRM,
    INVENTORY_CREATE,
    INVENTORY_EDIT,
    INVENTORY_PUBLISH,
    INVENTORY_VIEW,
    PRICES_EDIT,
    PRICES_VIEW,
)
from apps.core.pagination import paginate
from apps.pricing.services import resolve_visible_prices

from .catalog_selection import create_selection, resolve_selection
from .filters import effective_price_bounds
from .forms import ItemMediaForm, ItemStockForm, OwnerItemFilterForm, ProductItemForm, TierPriceForm
from .freshness import stock_view
from .selectors import filter_owned_lots, get_business_lot, lots_for_business
from .services import (
    InventoryError,
    add_lot_media,
    confirm_item_stock,
    create_product_item,
    delete_item,
    delete_lot_media,
    duplicate_item,
    item_has_commercial_history,
    reorder_lot_media,
    set_item_availability,
    set_item_visibility,
    set_primary_media,
    update_product_item,
)

logger = logging.getLogger(__name__)


def _price_spec(form: TierPriceForm) -> dict:
    return {
        "mode": form.cleaned_data["mode"],
        "amount": form.cleaned_data.get("amount"),
        "valid_for_days": form.cleaned_data.get("valid_for_days"),
        "special_amount": form.cleaned_data.get("special_amount"),
        "special_until": form.cleaned_data.get("special_until"),
    }


def _price_initial(lot, tier_code: str) -> dict:
    price = next((item for item in lot.prices.all() if item.tier.code == tier_code), None)
    if price is None:
        return {}
    return {
        "mode": price.mode,
        "amount": price.amount,
        "valid_for_days": price.price_valid_for_days,
        "special_amount": price.special_amount,
        "special_until": price.special_until,
    }


def _product_fields(form: ProductItemForm) -> dict:
    return {
        "stone": form.cleaned_data["stone"],
        "name_suffix": form.cleaned_data.get("name_suffix", ""),
        "pattern": form.cleaned_data.get("pattern", ""),
        "description_public": form.cleaned_data.get("description_public", ""),
        "description_professional": form.cleaned_data.get("description_professional", ""),
    }


def _item_fields(form: ProductItemForm, *, may_publish: bool, lot=None) -> dict:
    return {
        "processing_type": form.cleaned_data.get("processing_type", "ساب خورده"),
        "available_sqm": form.cleaned_data.get("available_sqm"),
        "stock_valid_for_days": form.cleaned_data["stock_valid_for_days"],
        "length_cm": form.cleaned_data.get("length_cm"),
        "width_cm": form.cleaned_data.get("width_cm"),
        "thickness_mm": form.thickness_mm,
        "min_sale_qty": form.cleaned_data.get("min_sale_qty") or Decimal("0"),
        "description": form.cleaned_data.get("description_professional", ""),
        "defect_notes": form.cleaned_data.get("defect_notes", ""),
        "availability_status": form.cleaned_data["availability_status"],
        "is_visible": (
            bool(form.cleaned_data.get("is_visible"))
            if may_publish
            else (lot.is_visible if lot is not None else False)
        ),
        "is_urgent_sale": bool(form.cleaned_data.get("is_urgent_sale")),
    }


def _seller_processing_suggestions(business) -> list[str]:
    return list(
        lots_for_business(business)
        .exclude(processing_type="")
        .order_by("processing_type")
        .values_list("processing_type", flat=True)
        .distinct()[:50]
    )


@business_login_required
@require_capability(INVENTORY_VIEW)
def lot_list(request: HttpRequest) -> HttpResponse:
    form = OwnerItemFilterForm(request.GET or None)
    spec = form.to_spec()
    base = lots_for_business(request.business)
    qs = filter_owned_lots(base, spec=spec, state=form.state_value)
    minimum, maximum = effective_price_bounds(base, spec=spec, audience="owner")
    page = paginate(request, qs)
    can_view_prices = request.membership.has_capability(PRICES_VIEW)
    rows = []
    for lot in page.object_list:
        prices = resolve_visible_prices(lot, "owner_staff", can_view_prices=can_view_prices)
        primary = next((item for item in lot.media.all() if item.is_primary), None) or next(
            iter(lot.media.all()), None
        )
        rows.append({"lot": lot, "stock": stock_view(lot), "prices": prices, "primary_media": primary})
    from apps.catalog.selectors import catalogs_for_business

    return render(
        request,
        "inventory/lot_list.html",
        {
            "filter_form": form,
            "rows": rows,
            "page": page,
            "can_view_prices": can_view_prices,
            "price_bounds": {"minimum": minimum, "maximum": maximum},
            "catalogs": catalogs_for_business(request.business),
        },
    )


@business_login_required
@require_capability(INVENTORY_VIEW)
def lot_detail(request: HttpRequest, lot_id) -> HttpResponse:
    lot = get_business_lot(request.business, lot_id)
    if lot is None:
        messages.error(request, "محصول یافت نشد.")
        return redirect("inventory:lot_list")
    can_view_prices = request.membership.has_capability(PRICES_VIEW)
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
            "share_url": request.build_absolute_uri(f"/p/{lot.public_token}/"),
            "has_history": item_has_commercial_history(lot),
        },
    )


def _product_initial(lot) -> dict:
    return {
        "stone": lot.product.stone,
        "name_suffix": lot.product.name_suffix,
        "applications": lot.product.applications.all(),
        "pattern": lot.product.pattern,
        "processing_type": lot.processing_type,
        "length_cm": lot.length_cm,
        "width_cm": lot.width_cm,
        "thickness_cm": lot.thickness_mm / Decimal("10") if lot.thickness_mm is not None else None,
        "available_sqm": lot.available_sqm,
        "stock_valid_for_days": lot.stock_valid_for_days,
        "min_sale_qty": lot.min_sale_qty,
        "description_public": lot.product.description_public,
        "description_professional": lot.product.description_professional,
        "defect_notes": lot.defect_notes,
        "availability_status": lot.availability_status,
        "is_visible": lot.is_visible,
        "is_urgent_sale": lot.is_urgent_sale,
    }


def _product_form_context(request, *, form, b2b_form, b2c_form, lot=None):
    return {
        "form": form,
        "b2b_form": b2b_form,
        "b2c_form": b2c_form,
        "lot": lot,
        "mode": "edit" if lot else "create",
        "can_edit_prices": request.membership.has_capability(PRICES_EDIT),
        "can_publish": request.membership.has_capability(INVENTORY_PUBLISH),
        "processing_suggestions": _seller_processing_suggestions(request.business),
    }


@business_login_required
@require_capability(INVENTORY_CREATE)
@require_http_methods(["GET", "POST"])
def product_create(request: HttpRequest) -> HttpResponse:
    can_price = request.membership.has_capability(PRICES_EDIT)
    form = ProductItemForm(request.POST or None, initial={"is_visible": True})
    b2b_form = TierPriceForm(request.POST or None, prefix="b2b", tier_label="قیمت همکار")
    b2c_form = TierPriceForm(request.POST or None, prefix="b2c", tier_label="قیمت مشتری")
    prices_ok = not can_price or (b2b_form.is_valid() and b2c_form.is_valid())
    if request.method == "POST" and form.is_valid() and prices_ok:
        try:
            lot = create_product_item(
                business=request.business,
                membership=request.membership,
                product_fields=_product_fields(form),
                item_fields=_item_fields(
                    form,
                    may_publish=request.membership.has_capability(INVENTORY_PUBLISH),
                ),
                applications=list(form.cleaned_data.get("applications") or []),
                b2b_price=_price_spec(b2b_form) if can_price else None,
                b2c_price=_price_spec(b2c_form) if can_price else None,
            )
        except InventoryError as exc:
            form.add_error(None, exc.message)
        else:
            messages.success(request, f"محصول با کد {lot.lot_code} ساخته شد.")
            return redirect("inventory:lot_detail", lot_id=lot.id)
    return render(
        request,
        "inventory/product_form.html",
        _product_form_context(request, form=form, b2b_form=b2b_form, b2c_form=b2c_form),
    )


@business_login_required
@require_capability(INVENTORY_EDIT)
@require_http_methods(["GET", "POST"])
def lot_edit(request: HttpRequest, lot_id) -> HttpResponse:
    lot = get_business_lot(request.business, lot_id)
    if lot is None:
        messages.error(request, "محصول یافت نشد.")
        return redirect("inventory:lot_list")
    can_price = request.membership.has_capability(PRICES_EDIT)
    form = ProductItemForm(request.POST or None, initial=_product_initial(lot))
    b2b_form = TierPriceForm(
        request.POST or None, prefix="b2b", tier_label="قیمت همکار", initial=_price_initial(lot, "b2b")
    )
    b2c_form = TierPriceForm(
        request.POST or None, prefix="b2c", tier_label="قیمت مشتری", initial=_price_initial(lot, "b2c")
    )
    prices_ok = not can_price or (b2b_form.is_valid() and b2c_form.is_valid())
    if request.method == "POST" and form.is_valid() and prices_ok:
        try:
            update_product_item(
                lot=lot,
                membership=request.membership,
                product_fields=_product_fields(form),
                item_fields=_item_fields(
                    form,
                    may_publish=request.membership.has_capability(INVENTORY_PUBLISH),
                    lot=lot,
                ),
                applications=list(form.cleaned_data.get("applications") or []),
                b2b_price=_price_spec(b2b_form) if can_price else None,
                b2c_price=_price_spec(b2c_form) if can_price else None,
            )
        except InventoryError as exc:
            form.add_error(None, exc.message)
        else:
            messages.success(request, "محصول به‌روزرسانی شد.")
            return redirect("inventory:lot_detail", lot_id=lot.id)
    return render(
        request,
        "inventory/product_form.html",
        _product_form_context(request, form=form, b2b_form=b2b_form, b2c_form=b2c_form, lot=lot),
    )


@business_login_required
@require_capability(INVENTORY_CONFIRM)
@require_http_methods(["GET", "POST"])
def lot_confirm_stock(request: HttpRequest, lot_id) -> HttpResponse:
    lot = get_business_lot(request.business, lot_id)
    if lot is None:
        messages.error(request, "محصول یافت نشد.")
        return redirect("inventory:lot_list")
    initial = {"available_sqm": lot.available_sqm, "stock_valid_for_days": lot.stock_valid_for_days}
    form = ItemStockForm(request.POST or None, initial=initial)
    if request.method == "POST":
        if request.POST.get("action") == "reconfirm" and lot.available_sqm is not None:
            available_sqm, valid_days, valid = lot.available_sqm, lot.stock_valid_for_days, True
        else:
            valid = form.is_valid()
            available_sqm = form.cleaned_data.get("available_sqm") if valid else None
            valid_days = form.cleaned_data.get("stock_valid_for_days") if valid else None
        if valid:
            try:
                confirm_item_stock(
                    lot=lot,
                    membership=request.membership,
                    available_sqm=available_sqm,
                    stock_valid_for_days=valid_days,
                )
            except InventoryError as exc:
                form.add_error(None, exc.message)
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
        return redirect("inventory:lot_list")
    try:
        set_item_visibility(
            lot=lot, membership=request.membership, is_visible=request.POST.get("visible") == "1"
        )
    except InventoryError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "وضعیت انتشار تغییر کرد.")
    return redirect("inventory:lot_detail", lot_id=lot.id)


@business_login_required
@require_capability(INVENTORY_EDIT)
@require_http_methods(["GET", "POST"])
def lot_delete(request: HttpRequest, lot_id) -> HttpResponse:
    lot = get_business_lot(request.business, lot_id)
    if lot is None:
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
        request, "inventory/lot_confirm_delete.html",
        {"lot": lot, "has_history": item_has_commercial_history(lot)},
    )


@business_login_required
@require_capability(INVENTORY_CREATE)
@require_POST
def lot_duplicate(request: HttpRequest, lot_id) -> HttpResponse:
    lot = get_business_lot(request.business, lot_id)
    if lot is None:
        return redirect("inventory:lot_list")
    try:
        clone = duplicate_item(lot=lot, membership=request.membership)
    except InventoryError as exc:
        messages.error(request, exc.message)
        return redirect("inventory:lot_detail", lot_id=lot.id)
    messages.success(request, f"کپی با کد {clone.lot_code} ساخته شد؛ فروش ویژه کپی نشد.")
    return redirect("inventory:lot_edit", lot_id=clone.id)


@business_login_required
@require_capability(INVENTORY_EDIT)
@require_http_methods(["GET", "POST"])
def lot_media(request: HttpRequest, lot_id) -> HttpResponse:
    lot = get_business_lot(request.business, lot_id)
    if lot is None:
        return redirect("inventory:lot_list")
    form = ItemMediaForm(request.POST or None, request.FILES or None)
    if request.method == "POST":
        action = request.POST.get("action", "upload")
        try:
            if action == "delete":
                delete_lot_media(lot=lot, membership=request.membership, media_id=request.POST.get("media_id"))
            elif action == "primary":
                set_primary_media(lot=lot, membership=request.membership, media_id=request.POST.get("media_id"))
            elif action == "reorder":
                reorder_lot_media(lot=lot, membership=request.membership, media_ids=request.POST.getlist("order"))
            elif form.is_valid() and request.FILES.get("images"):
                add_lot_media(
                    lot=lot,
                    membership=request.membership,
                    upload=request.FILES["images"],
                    is_primary=form.cleaned_data.get("is_primary", False),
                )
        except InventoryError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(request, "رسانه‌ها به‌روزرسانی شدند.")
            return redirect("inventory:lot_media", lot_id=lot.id)
    return render(request, "inventory/lot_media.html", {"lot": lot, "form": form, "media_items": lot.media.all()})


@business_login_required
@require_capability(CATALOG_MANAGE)
@require_POST
def catalog_selection_start(request: HttpRequest) -> HttpResponse:
    query = QueryDict(request.POST.get("filter_query", ""))
    # Keep an empty query bound: it means the valid filter "all inventory".
    filter_form = OwnerItemFilterForm(query)
    scope = "filter" if request.POST.get("selection_scope") == "filter" else "selected"
    if scope == "filter" and not filter_form.is_valid():
        messages.error(request, "فیلتر انتخاب محصولات معتبر نیست؛ دوباره تلاش کنید.")
        return redirect("inventory:lot_list")
    spec = filter_form.to_spec()
    state = filter_form.state_value
    owned_ids = []
    if scope == "selected":
        requested_ids = request.POST.getlist("lot_ids")
        if not requested_ids:
            messages.error(request, "حداقل یک محصول را انتخاب کنید.")
            return redirect("inventory:lot_list")
        try:
            owned = {
                str(pk): pk
                for pk in lots_for_business(request.business)
                .filter(pk__in=requested_ids)
                .values_list("pk", flat=True)
            }
        except (DjangoValidationError, TypeError, ValueError):
            messages.error(request, "یک یا چند محصول انتخاب‌شده معتبر نیست.")
            return redirect("inventory:lot_list")
        ordered_keys = list(dict.fromkeys(str(item) for item in requested_ids))
        if any(key not in owned for key in ordered_keys):
            messages.error(request, "یک یا چند محصول انتخاب‌شده متعلق به کسب‌وکار شما نیست.")
            return redirect("inventory:lot_list")
        owned_ids = [owned[key] for key in ordered_keys]
    record = {
        "scope": scope,
        "lot_ids": [str(item) for item in owned_ids],
        "filter": spec.to_dict(),
        "state": state,
    }
    resolved_ids = list(
        resolve_selection(business=request.business, record=record).values_list("pk", flat=True)
    )
    if not resolved_ids:
        messages.error(request, "هیچ محصولی در این انتخاب باقی نمانده است.")
        return redirect("inventory:lot_list")
    catalog_id = request.POST.get("catalog_id")
    if catalog_id:
        from apps.catalog.models import CustomCatalog
        from apps.catalog.services import CatalogError, add_catalog_lots

        catalog = CustomCatalog.objects.filter(pk=catalog_id, business=request.business).first()
        if catalog is None:
            messages.error(request, "کاتالوگ یافت نشد.")
            return redirect("inventory:lot_list")
        try:
            add_catalog_lots(
                catalog=catalog,
                membership=request.membership,
                lot_ids=resolved_ids,
            )
        except CatalogError as exc:
            messages.error(request, exc.message)
            return redirect("inventory:lot_list")
        else:
            messages.success(request, "محصولات به کاتالوگ اضافه شدند.")
            return redirect("catalog_manage:detail", catalog_id=catalog.id)
    token = create_selection(
        request,
        business=request.business,
        scope=scope,
        lot_ids=[str(item) for item in owned_ids],
        spec=spec,
        state=state,
    )
    return redirect(f"{reverse('catalog_manage:create')}?selection={token}")
