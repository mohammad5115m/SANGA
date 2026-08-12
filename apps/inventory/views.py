from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
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
from apps.contacts.models import Contact
from apps.contacts.selectors import get_contact
from apps.pricing.selectors import contact_prices_for_lot
from apps.pricing.services import (
    PricingError,
    remove_contact_price,
    resolve_visible_prices,
    set_contact_price,
)

from .forms import (
    ContactPriceForm,
    InventoryFilterForm,
    LotDetailsForm,
    LotEditForm,
    LotMediaForm,
    LotPricesForm,
    LotQuantityForm,
    LotVisibilityForm,
    ProductPickForm,
)
from .freshness import evaluate_freshness
from .selectors import filter_lots, get_business_lot, lots_for_business, products_for_business
from .services import (
    InventoryError,
    add_lot_media,
    archive_lot,
    confirm_lot_inventory,
    create_draft_lot,
    create_or_get_product,
    duplicate_lot,
    hide_lot,
    mark_lot_sold,
    set_visibility_and_status,
    update_lot_fields,
    update_lot_prices,
)

logger = logging.getLogger(__name__)

WIZARD_SESSION_KEY = "inventory_quick_add"


def _wizard_data(request: HttpRequest) -> dict:
    return request.session.get(WIZARD_SESSION_KEY, {})


def _save_wizard(request: HttpRequest, data: dict) -> None:
    request.session[WIZARD_SESSION_KEY] = data
    request.session.modified = True


def _clear_wizard(request: HttpRequest) -> None:
    request.session.pop(WIZARD_SESSION_KEY, None)


def _parse_decimal(value: str | None) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


@business_login_required
@require_capability(INVENTORY_VIEW)
def lot_list(request: HttpRequest) -> HttpResponse:
    form = InventoryFilterForm(request.GET or None)
    qs = lots_for_business(request.business)
    if form.is_valid():
        qs = filter_lots(
            qs,
            q=form.cleaned_data.get("q", ""),
            status=form.cleaned_data.get("status", ""),
            visibility=form.cleaned_data.get("visibility", ""),
            freshness=form.cleaned_data.get("freshness", ""),
        )
    can_view_prices = request.membership.has_capability(PRICES_VIEW)
    rows = []
    for lot in qs[:100]:
        freshness = evaluate_freshness(lot)
        prices = resolve_visible_prices(lot, "owner_staff", can_view_prices=can_view_prices)
        primary = next((m for m in lot.media.all() if m.is_primary), None) or next(iter(lot.media.all()), None)
        rows.append({"lot": lot, "freshness": freshness, "prices": prices, "primary_media": primary})
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
        messages.error(request, "محموله یافت نشد.")
        return redirect("inventory:lot_list")
    can_view_prices = request.membership.has_capability(PRICES_VIEW)
    context = {
        "lot": lot,
        "freshness": evaluate_freshness(lot),
        "prices": resolve_visible_prices(lot, "owner_staff", can_view_prices=can_view_prices),
        "media_items": lot.media.all(),
        "can_view_prices": can_view_prices,
        "can_edit_prices": request.membership.has_capability(PRICES_EDIT),
    }
    return render(request, "inventory/lot_detail.html", context)


@business_login_required
@require_capability(INVENTORY_EDIT)
@require_http_methods(["GET", "POST"])
def lot_edit(request: HttpRequest, lot_id) -> HttpResponse:
    lot = get_business_lot(request.business, lot_id)
    if lot is None:
        messages.error(request, "محموله یافت نشد.")
        return redirect("inventory:lot_list")

    form = LotEditForm(request.POST or None, instance=lot, business=request.business)
    price_form = LotPricesForm(
        request.POST or None,
        prefix="price",
        initial={
            "b2b_amount": getattr(lot.prices.filter(tier__code="b2b").first(), "amount", None),
            "b2c_amount": getattr(lot.prices.filter(tier__code="b2c").first(), "amount", None),
        },
    )
    if request.method == "POST" and form.is_valid():
        try:
            update_lot_fields(
                lot=lot,
                membership=request.membership,
                warehouse=form.cleaned_data["warehouse"],
                grade=form.cleaned_data.get("grade", ""),
                processing_type=form.cleaned_data.get("processing_type", ""),
                available_sqm=form.cleaned_data["available_sqm"],
                slab_count=form.cleaned_data.get("slab_count"),
                length_cm=form.cleaned_data.get("length_cm"),
                width_cm=form.cleaned_data.get("width_cm"),
                thickness_mm=form.cleaned_data.get("thickness_mm"),
                description=form.cleaned_data.get("description", ""),
                defect_notes=form.cleaned_data.get("defect_notes", ""),
                is_urgent_sale=form.cleaned_data.get("is_urgent_sale", False),
                is_featured=form.cleaned_data.get("is_featured", False),
            )
            set_visibility_and_status(
                lot=lot,
                membership=request.membership,
                visibility=form.cleaned_data["visibility"],
            )
            new_status = form.cleaned_data["status"]
            if new_status != lot.status:
                # Status controls what is publicly visible, so it needs the
                # publish capability just like visibility changes.
                if request.membership.has_capability(INVENTORY_PUBLISH):
                    lot.status = new_status
                    lot.save(update_fields=["status", "updated_at"])
                else:
                    messages.warning(request, "تغییر وضعیت نیاز به دسترسی انتشار دارد و اعمال نشد.")
            if request.membership.has_capability(PRICES_EDIT) and price_form.is_valid():
                update_lot_prices(
                    lot=lot,
                    membership=request.membership,
                    b2b_amount=price_form.cleaned_data["b2b_amount"],
                    b2c_amount=price_form.cleaned_data["b2c_amount"],
                    currency=price_form.cleaned_data.get("currency") or "IRR",
                )
        except InventoryError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(request, "محموله به‌روزرسانی شد.")
            return redirect("inventory:lot_detail", lot_id=lot.id)
    return render(
        request,
        "inventory/lot_edit.html",
        {"lot": lot, "form": form, "price_form": price_form},
    )


@business_login_required
@require_capability(PRICES_EDIT)
@require_http_methods(["GET", "POST"])
def lot_partner_prices(request: HttpRequest, lot_id) -> HttpResponse:
    """Per-partner prices for one lot: add, change, or remove an override.

    The capability and the tenant checks are re-applied inside
    ``pricing.services``; the decorator here only keeps the screen out of sight.
    """
    lot = get_business_lot(request.business, lot_id)
    if lot is None:
        messages.error(request, "محموله یافت نشد.")
        return redirect("inventory:lot_list")

    form = ContactPriceForm(business=request.business)
    if request.method == "POST":
        action = request.POST.get("action", "save")
        try:
            if action == "remove":
                contact = get_contact(request.business, request.POST.get("contact", ""))
                remove_contact_price(lot=lot, contact=contact, membership=request.membership)
                messages.success(request, "قیمت اختصاصی حذف شد.")
                return redirect("inventory:lot_partner_prices", lot_id=lot.id)

            form = ContactPriceForm(request.POST, business=request.business)
            if form.is_valid():
                set_contact_price(
                    lot=lot,
                    contact=form.cleaned_data["contact"],
                    membership=request.membership,
                    amount=form.cleaned_data.get("amount"),
                    currency=form.cleaned_data.get("currency") or "IRR",
                    unit=form.cleaned_data["unit"],
                )
                messages.success(request, "قیمت اختصاصی ذخیره شد.")
                return redirect("inventory:lot_partner_prices", lot_id=lot.id)
        except (Contact.DoesNotExist, ValidationError, ValueError):
            messages.error(request, "مخاطب یافت نشد.")
            return redirect("inventory:lot_partner_prices", lot_id=lot.id)
        except PricingError as exc:
            messages.error(request, exc.message)
        except Exception:
            logger.exception("Contact price update failed lot=%s", lot.id)
            messages.error(request, "ذخیره قیمت اختصاصی با خطا روبه‌رو شد؛ دوباره تلاش کنید.")

    return render(
        request,
        "inventory/lot_partner_prices.html",
        {
            "lot": lot,
            "form": form,
            "overrides": contact_prices_for_lot(request.business, lot),
        },
    )


@business_login_required
@require_capability(INVENTORY_CONFIRM)
@require_POST
def lot_confirm(request: HttpRequest, lot_id) -> HttpResponse:
    lot = get_business_lot(request.business, lot_id)
    if lot is None:
        messages.error(request, "محموله یافت نشد.")
        return redirect("inventory:lot_list")
    try:
        confirm_lot_inventory(lot=lot, membership=request.membership)
    except InventoryError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "موجودی تأیید شد.")
    return redirect("inventory:lot_detail", lot_id=lot.id)


@business_login_required
@require_capability(INVENTORY_CREATE)
@require_POST
def lot_duplicate(request: HttpRequest, lot_id) -> HttpResponse:
    lot = get_business_lot(request.business, lot_id)
    if lot is None:
        messages.error(request, "محموله یافت نشد.")
        return redirect("inventory:lot_list")
    try:
        clone = duplicate_lot(lot=lot, membership=request.membership)
    except InventoryError as exc:
        messages.error(request, exc.message)
        return redirect("inventory:lot_detail", lot_id=lot.id)
    messages.success(request, f"کپی ایجاد شد: {clone.lot_code}")
    return redirect("inventory:lot_edit", lot_id=clone.id)


@business_login_required
@require_capability(INVENTORY_EDIT)
@require_POST
def lot_mark_sold(request: HttpRequest, lot_id) -> HttpResponse:
    lot = get_business_lot(request.business, lot_id)
    if lot is None:
        messages.error(request, "محموله یافت نشد.")
        return redirect("inventory:lot_list")
    try:
        mark_lot_sold(lot=lot, membership=request.membership)
    except InventoryError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "محموله فروخته‌شده علامت خورد.")
    return redirect("inventory:lot_detail", lot_id=lot.id)


@business_login_required
@require_capability(INVENTORY_EDIT)
@require_POST
def lot_hide(request: HttpRequest, lot_id) -> HttpResponse:
    lot = get_business_lot(request.business, lot_id)
    if lot is None:
        messages.error(request, "محموله یافت نشد.")
        return redirect("inventory:lot_list")
    try:
        hide_lot(lot=lot, membership=request.membership)
    except InventoryError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "محموله مخفی شد.")
    return redirect("inventory:lot_list")


@business_login_required
@require_capability(INVENTORY_EDIT)
@require_POST
def lot_archive(request: HttpRequest, lot_id) -> HttpResponse:
    lot = get_business_lot(request.business, lot_id)
    if lot is None:
        messages.error(request, "محموله یافت نشد.")
        return redirect("inventory:lot_list")
    try:
        archive_lot(lot=lot, membership=request.membership)
    except InventoryError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "محموله بایگانی شد.")
    return redirect("inventory:lot_list")


@business_login_required
@require_capability(INVENTORY_CREATE)
@require_http_methods(["GET", "POST"])
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
        {"form": form, "step": 1, "total_steps": 7, "products": products_for_business(request.business)[:30]},
    )


@business_login_required
@require_capability(INVENTORY_CREATE)
@require_http_methods(["GET", "POST"])
def quick_add_details(request: HttpRequest) -> HttpResponse:
    data = _wizard_data(request)
    if not data.get("product_id"):
        return redirect("inventory:quick_add_product")
    form = LotDetailsForm(request.POST or None, business=request.business)
    if request.method == "POST" and form.is_valid():
        data.update(
            {
                "warehouse_id": str(form.cleaned_data["warehouse"].id),
                "lot_code": form.cleaned_data.get("lot_code") or "",
                "grade": form.cleaned_data.get("grade") or "",
                "processing_type": form.cleaned_data.get("processing_type") or "",
                "description": form.cleaned_data.get("description") or "",
            }
        )
        _save_wizard(request, data)
        return redirect("inventory:quick_add_quantity")
    return render(request, "inventory/wizard/details.html", {"form": form, "step": 2, "total_steps": 7})


@business_login_required
@require_capability(INVENTORY_CREATE)
@require_http_methods(["GET", "POST"])
def quick_add_quantity(request: HttpRequest) -> HttpResponse:
    data = _wizard_data(request)
    if not data.get("warehouse_id"):
        return redirect("inventory:quick_add_details")
    form = LotQuantityForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        from apps.businesses.models import Warehouse
        from .models import Product

        product = Product.objects.filter(business=request.business, pk=data["product_id"]).first()
        warehouse = Warehouse.objects.filter(business=request.business, pk=data["warehouse_id"]).first()
        if product is None or warehouse is None:
            messages.error(request, "اطلاعات ویزارد ناقص است. دوباره شروع کنید.")
            return redirect("inventory:quick_add_start")
        try:
            if data.get("lot_id"):
                lot = get_business_lot(request.business, data["lot_id"])
                if lot is None:
                    raise InventoryError("محموله پیش‌نویس یافت نشد.")
                update_lot_fields(
                    lot=lot,
                    membership=request.membership,
                    available_sqm=form.cleaned_data["available_sqm"],
                    original_sqm=form.cleaned_data["available_sqm"],
                    slab_count=form.cleaned_data.get("slab_count"),
                    length_cm=form.cleaned_data.get("length_cm"),
                    width_cm=form.cleaned_data.get("width_cm"),
                    thickness_mm=form.cleaned_data.get("thickness_mm"),
                )
            else:
                lot = create_draft_lot(
                    business=request.business,
                    membership=request.membership,
                    product=product,
                    warehouse=warehouse,
                    lot_code=data.get("lot_code", ""),
                    grade=data.get("grade", ""),
                    processing_type=data.get("processing_type", ""),
                    description=data.get("description", ""),
                    available_sqm=form.cleaned_data["available_sqm"],
                    original_sqm=form.cleaned_data["available_sqm"],
                    slab_count=form.cleaned_data.get("slab_count"),
                    length_cm=form.cleaned_data.get("length_cm"),
                    width_cm=form.cleaned_data.get("width_cm"),
                    thickness_mm=form.cleaned_data.get("thickness_mm"),
                )
        except InventoryError as exc:
            form.add_error(None, exc.message)
        else:
            data["lot_id"] = str(lot.id)
            _save_wizard(request, data)
            return redirect("inventory:quick_add_media")
    return render(request, "inventory/wizard/quantity.html", {"form": form, "step": 3, "total_steps": 7})


@business_login_required
@require_capability(INVENTORY_CREATE)
@require_http_methods(["GET", "POST"])
def quick_add_media(request: HttpRequest) -> HttpResponse:
    data = _wizard_data(request)
    lot = get_business_lot(request.business, data.get("lot_id")) if data.get("lot_id") else None
    if lot is None:
        return redirect("inventory:quick_add_quantity")
    form = LotMediaForm(request.POST or None, request.FILES or None)
    if request.method == "POST":
        if "skip" in request.POST:
            return redirect("inventory:quick_add_prices")
        if form.is_valid() and request.FILES.get("images"):
            try:
                add_lot_media(
                    lot=lot,
                    membership=request.membership,
                    upload=request.FILES["images"],
                    is_primary=form.cleaned_data.get("is_primary", True),
                )
            except InventoryError as exc:
                form.add_error(None, exc.message)
            else:
                messages.success(request, "رسانه اضافه شد.")
                if "add_another" in request.POST:
                    return redirect("inventory:quick_add_media")
                return redirect("inventory:quick_add_prices")
    return render(
        request,
        "inventory/wizard/media.html",
        {"form": form, "lot": lot, "media_items": lot.media.all(), "step": 4, "total_steps": 7},
    )


@business_login_required
@require_capability(INVENTORY_CREATE)
@require_http_methods(["GET", "POST"])
def quick_add_prices(request: HttpRequest) -> HttpResponse:
    data = _wizard_data(request)
    lot = get_business_lot(request.business, data.get("lot_id")) if data.get("lot_id") else None
    if lot is None:
        return redirect("inventory:quick_add_quantity")
    form = LotPricesForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            update_lot_prices(
                lot=lot,
                membership=request.membership,
                b2b_amount=form.cleaned_data["b2b_amount"],
                b2c_amount=form.cleaned_data["b2c_amount"],
                currency=form.cleaned_data.get("currency") or "IRR",
            )
        except InventoryError as exc:
            # Owners creating inventory usually have prices.edit; staff may not.
            if request.membership.has_capability(PRICES_EDIT):
                form.add_error(None, exc.message)
            else:
                messages.warning(request, "قیمت ذخیره نشد؛ دسترسی ویرایش قیمت ندارید.")
                return redirect("inventory:quick_add_visibility")
        else:
            return redirect("inventory:quick_add_visibility")
    return render(request, "inventory/wizard/prices.html", {"form": form, "lot": lot, "step": 5, "total_steps": 7})


@business_login_required
@require_capability(INVENTORY_CREATE)
@require_http_methods(["GET", "POST"])
def quick_add_visibility(request: HttpRequest) -> HttpResponse:
    data = _wizard_data(request)
    lot = get_business_lot(request.business, data.get("lot_id")) if data.get("lot_id") else None
    if lot is None:
        return redirect("inventory:quick_add_quantity")
    from .models import InventoryLot

    form = LotVisibilityForm(
        request.POST or None,
        initial={"visibility": InventoryLot.Visibility.PUBLIC},
    )
    if request.method == "POST" and form.is_valid():
        data["visibility"] = form.cleaned_data["visibility"]
        data["is_urgent_sale"] = form.cleaned_data.get("is_urgent_sale", False)
        data["is_featured"] = form.cleaned_data.get("is_featured", False)
        _save_wizard(request, data)
        return redirect("inventory:quick_add_review")
    return render(request, "inventory/wizard/visibility.html", {"form": form, "lot": lot, "step": 6, "total_steps": 7})


@business_login_required
@require_capability(INVENTORY_CREATE)
@require_http_methods(["GET", "POST"])
def quick_add_review(request: HttpRequest) -> HttpResponse:
    from .models import InventoryLot

    data = _wizard_data(request)
    lot = get_business_lot(request.business, data.get("lot_id")) if data.get("lot_id") else None
    if lot is None:
        return redirect("inventory:quick_add_start")

    if request.method == "POST":
        action = request.POST.get("action", "publish")
        try:
            update_lot_fields(
                lot=lot,
                membership=request.membership,
                is_urgent_sale=bool(data.get("is_urgent_sale")),
                is_featured=bool(data.get("is_featured")),
            )
            set_visibility_and_status(
                lot=lot,
                membership=request.membership,
                visibility=data.get("visibility") or InventoryLot.Visibility.PRIVATE,
                publish=(action == "publish"),
                save_as_draft=(action == "draft"),
            )
        except InventoryError as exc:
            messages.error(request, exc.message)
        else:
            _clear_wizard(request)
            messages.success(request, "محموله ذخیره شد." if action == "draft" else "محموله منتشر شد.")
            return redirect("inventory:lot_detail", lot_id=lot.id)

    can_view_prices = request.membership.has_capability(PRICES_VIEW)
    visibility_value = data.get("visibility") or InventoryLot.Visibility.PRIVATE
    try:
        visibility_label = InventoryLot.Visibility(visibility_value).label
    except ValueError:
        visibility_label = visibility_value
    return render(
        request,
        "inventory/wizard/review.html",
        {
            "lot": lot,
            "prices": resolve_visible_prices(lot, "owner_staff", can_view_prices=can_view_prices),
            "freshness": evaluate_freshness(lot),
            "visibility": visibility_value,
            "visibility_label": visibility_label,
            "step": 7,
            "total_steps": 7,
        },
    )
