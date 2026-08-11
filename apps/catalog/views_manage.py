from __future__ import annotations

import logging

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.businesses.decorators import business_login_required, require_capability
from apps.businesses.permissions import CATALOG_MANAGE, INQUIRIES_RESPOND, INQUIRIES_VIEW
from apps.inquiries.models import Inquiry

from .forms import CustomCatalogForm
from .models import CustomCatalog
from .selectors import catalogs_for_business
from .services import CatalogError, create_custom_catalog, set_catalog_lots

logger = logging.getLogger(__name__)


@business_login_required
@require_capability(CATALOG_MANAGE)
def catalog_list(request: HttpRequest) -> HttpResponse:
    catalogs = catalogs_for_business(request.business)
    return render(request, "catalog/manage_list.html", {"catalogs": catalogs})


@business_login_required
@require_capability(CATALOG_MANAGE)
@require_http_methods(["GET", "POST"])
def catalog_create(request: HttpRequest) -> HttpResponse:
    form = CustomCatalogForm(request.POST or None, business=request.business)
    if request.method == "POST" and form.is_valid():
        try:
            catalog = create_custom_catalog(
                business=request.business,
                membership=request.membership,
                title=form.cleaned_data["title"],
                customer_name=form.cleaned_data.get("customer_name", ""),
                custom_message=form.cleaned_data.get("custom_message", ""),
                expires_at=form.cleaned_data.get("expires_at"),
                lot_ids=[lot.id for lot in form.cleaned_data.get("lots", [])],
            )
            catalog.is_active = form.cleaned_data.get("is_active", True)
            catalog.save(update_fields=["is_active", "updated_at"])
        except CatalogError as exc:
            form.add_error(None, exc.message)
        except Exception:
            logger.exception("Catalog create failed")
            form.add_error(None, "ایجاد کاتالوگ با خطا روبه‌رو شد.")
        else:
            messages.success(request, "کاتالوگ ساخته شد.")
            return redirect("catalog_manage:detail", catalog_id=catalog.id)
    return render(request, "catalog/manage_form.html", {"form": form, "mode": "create"})


@business_login_required
@require_capability(CATALOG_MANAGE)
@require_http_methods(["GET", "POST"])
def catalog_edit(request: HttpRequest, catalog_id) -> HttpResponse:
    catalog = get_object_or_404(CustomCatalog, pk=catalog_id, business=request.business)
    form = CustomCatalogForm(request.POST or None, instance=catalog, business=request.business)
    if request.method == "POST" and form.is_valid():
        try:
            catalog.title = form.cleaned_data["title"]
            catalog.customer_name = form.cleaned_data.get("customer_name", "")
            catalog.custom_message = form.cleaned_data.get("custom_message", "")
            catalog.expires_at = form.cleaned_data.get("expires_at")
            catalog.is_active = form.cleaned_data.get("is_active", True)
            catalog.save()
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
    return render(request, "catalog/manage_form.html", {"form": form, "mode": "edit", "catalog": catalog})


@business_login_required
@require_capability(CATALOG_MANAGE)
def catalog_detail(request: HttpRequest, catalog_id) -> HttpResponse:
    catalog = get_object_or_404(
        CustomCatalog.objects.prefetch_related("items__lot__product"),
        pk=catalog_id,
        business=request.business,
    )
    share_url = request.build_absolute_uri(f"/c/{catalog.share_token}/")
    storefront_url = request.build_absolute_uri(f"/s/{request.business.slug}/")
    return render(
        request,
        "catalog/manage_detail.html",
        {
            "catalog": catalog,
            "share_url": share_url,
            "storefront_url": storefront_url,
        },
    )


@business_login_required
@require_capability(INQUIRIES_VIEW)
def inquiry_inbox(request: HttpRequest) -> HttpResponse:
    inquiries = (
        Inquiry.objects.filter(business=request.business)
        .select_related("lot", "lot__product", "custom_catalog")
        .order_by("-created_at")[:100]
    )
    return render(
        request,
        "catalog/inquiry_inbox.html",
        {"inquiries": inquiries, "status_choices": Inquiry.Status.choices},
    )


@business_login_required
@require_capability(INQUIRIES_RESPOND)
@require_POST
def inquiry_update_status(request: HttpRequest, inquiry_id) -> HttpResponse:
    inquiry = get_object_or_404(Inquiry, pk=inquiry_id, business=request.business)
    new_status = request.POST.get("status", "")
    if new_status not in Inquiry.Status.values:
        messages.error(request, "وضعیت انتخاب‌شده معتبر نیست.")
        return redirect("catalog_manage:inquiries")

    inquiry.status = new_status
    now = timezone.now()
    if inquiry.viewed_at is None:
        inquiry.viewed_at = now
    if new_status == Inquiry.Status.CONTACTED and inquiry.contacted_at is None:
        inquiry.contacted_at = now
    inquiry.save(update_fields=["status", "viewed_at", "contacted_at", "updated_at"])
    messages.success(request, "وضعیت استعلام به‌روزرسانی شد.")
    return redirect("catalog_manage:inquiries")
