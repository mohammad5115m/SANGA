from __future__ import annotations

import logging

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from apps.businesses.decorators import business_login_required, require_capability
from apps.businesses.permissions import CATALOG_MANAGE
from apps.inventory.forms import ItemFilterForm

from .forms import CustomCatalogForm
from .models import CustomCatalog
from .selectors import catalogs_for_business, resolve_catalog
from .services import (
    CatalogError,
    create_custom_catalog,
    set_catalog_exclusions,
    set_catalog_lots,
    update_catalog,
)

logger = logging.getLogger(__name__)


def _rules_from(form: CustomCatalogForm, request: HttpRequest) -> dict:
    """Read the rule fields off the same filter form the search bar uses."""
    filter_form = ItemFilterForm(request.POST or None, prefix="rule")
    return filter_form.to_spec().to_dict()


@business_login_required
@require_capability(CATALOG_MANAGE)
def catalog_list(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "catalog/manage_list.html",
        {"catalogs": catalogs_for_business(request.business)},
    )


@business_login_required
@require_capability(CATALOG_MANAGE)
@require_http_methods(["GET", "POST"])
def catalog_create(request: HttpRequest) -> HttpResponse:
    form = CustomCatalogForm(request.POST or None, business=request.business)
    rule_form = ItemFilterForm(request.POST or None, prefix="rule")

    if request.method == "POST" and form.is_valid():
        try:
            catalog = create_custom_catalog(
                business=request.business,
                membership=request.membership,
                title=form.cleaned_data["title"],
                customer_name=form.cleaned_data.get("customer_name", ""),
                custom_message=form.cleaned_data.get("custom_message", ""),
                expires_at=form.cleaned_data.get("expires_at"),
                mode=form.cleaned_data["mode"],
                rules=_rules_from(form, request),
                lot_ids=[lot.id for lot in form.cleaned_data.get("lots", [])],
            )
            if not form.cleaned_data.get("is_active", True):
                update_catalog(catalog=catalog, membership=request.membership, is_active=False)
        except CatalogError as exc:
            form.add_error(None, exc.message)
        except Exception:
            logger.exception("Catalog create failed")
            form.add_error(None, "ایجاد کاتالوگ با خطا روبه‌رو شد.")
        else:
            messages.success(request, "کاتالوگ ساخته شد.")
            return redirect("catalog_manage:detail", catalog_id=catalog.id)

    return render(
        request,
        "catalog/manage_form.html",
        {"form": form, "rule_form": rule_form, "mode": "create"},
    )


@business_login_required
@require_capability(CATALOG_MANAGE)
@require_http_methods(["GET", "POST"])
def catalog_edit(request: HttpRequest, catalog_id) -> HttpResponse:
    catalog = get_object_or_404(CustomCatalog, pk=catalog_id, business=request.business)
    form = CustomCatalogForm(request.POST or None, instance=catalog, business=request.business)
    rule_form = ItemFilterForm(
        request.POST or None,
        prefix="rule",
        initial=catalog.rules if not request.POST else None,
    )

    if request.method == "POST" and form.is_valid():
        try:
            update_catalog(
                catalog=catalog,
                membership=request.membership,
                title=form.cleaned_data["title"],
                customer_name=form.cleaned_data.get("customer_name", ""),
                custom_message=form.cleaned_data.get("custom_message", ""),
                expires_at=form.cleaned_data.get("expires_at"),
                is_active=form.cleaned_data.get("is_active", True),
                mode=form.cleaned_data["mode"],
                rules=_rules_from(form, request),
            )
            set_catalog_lots(
                catalog=catalog,
                membership=request.membership,
                lot_ids=[lot.id for lot in form.cleaned_data.get("lots", [])],
            )
        except CatalogError as exc:
            form.add_error(None, exc.message)
        else:
            messages.success(request, "کاتالوگ به‌روزرسانی شد.")
            return redirect("catalog_manage:detail", catalog_id=catalog.id)

    return render(
        request,
        "catalog/manage_form.html",
        {"form": form, "rule_form": rule_form, "mode": "edit", "catalog": catalog},
    )


@business_login_required
@require_capability(CATALOG_MANAGE)
def catalog_detail(request: HttpRequest, catalog_id) -> HttpResponse:
    catalog = get_object_or_404(CustomCatalog, pk=catalog_id, business=request.business)
    return render(
        request,
        "catalog/manage_detail.html",
        {
            "catalog": catalog,
            # Resolved live, exactly as the public link will render it, so the
            # seller sees what the customer will see rather than what they picked.
            "items": resolve_catalog(catalog),
            "excluded": catalog.items.filter(inclusion="exclude").select_related("lot__product"),
            "share_url": request.build_absolute_uri(f"/c/{catalog.share_token}/"),
            "storefront_url": request.build_absolute_uri(f"/s/{request.business.slug}/"),
        },
    )


@business_login_required
@require_capability(CATALOG_MANAGE)
@require_POST
def catalog_exclude(request: HttpRequest, catalog_id) -> HttpResponse:
    """Drop one product out of a rule-based catalog without changing the rule."""
    catalog = get_object_or_404(CustomCatalog, pk=catalog_id, business=request.business)
    existing = list(
        catalog.items.filter(inclusion="exclude").values_list("lot_id", flat=True)
    )
    lot_id = request.POST.get("lot_id")
    if lot_id and lot_id not in {str(pk) for pk in existing}:
        existing.append(lot_id)
    try:
        set_catalog_exclusions(catalog=catalog, membership=request.membership, lot_ids=existing)
    except CatalogError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "محصول از این کاتالوگ حذف شد.")
    return redirect("catalog_manage:detail", catalog_id=catalog.id)


@business_login_required
@require_capability(CATALOG_MANAGE)
@require_POST
def catalog_unexclude(request: HttpRequest, catalog_id) -> HttpResponse:
    catalog = get_object_or_404(CustomCatalog, pk=catalog_id, business=request.business)
    lot_id = str(request.POST.get("lot_id") or "")
    remaining = [
        pk
        for pk in catalog.items.filter(inclusion="exclude").values_list("lot_id", flat=True)
        if str(pk) != lot_id
    ]
    try:
        set_catalog_exclusions(catalog=catalog, membership=request.membership, lot_ids=remaining)
    except CatalogError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "محصول دوباره به کاتالوگ برگشت.")
    return redirect("catalog_manage:detail", catalog_id=catalog.id)


@business_login_required
@require_capability(CATALOG_MANAGE)
@require_POST
def catalog_toggle_active(request: HttpRequest, catalog_id) -> HttpResponse:
    catalog = get_object_or_404(CustomCatalog, pk=catalog_id, business=request.business)
    try:
        update_catalog(
            catalog=catalog,
            membership=request.membership,
            is_active=not catalog.is_active,
        )
    except CatalogError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "کاتالوگ فعال شد." if catalog.is_active else "کاتالوگ غیرفعال شد.")
    return redirect("catalog_manage:detail", catalog_id=catalog.id)


@business_login_required
@require_capability(CATALOG_MANAGE)
@require_http_methods(["GET", "POST"])
def catalog_delete(request: HttpRequest, catalog_id) -> HttpResponse:
    catalog = get_object_or_404(CustomCatalog, pk=catalog_id, business=request.business)
    if request.method == "POST":
        catalog.delete()
        messages.success(request, "کاتالوگ حذف شد.")
        return redirect("catalog_manage:list")
    return render(request, "catalog/manage_confirm_delete.html", {"catalog": catalog})
