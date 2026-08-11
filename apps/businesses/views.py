from __future__ import annotations

import logging

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .decorators import business_login_required, require_capability
from .forms import BusinessCreateForm, BusinessProfileForm, WarehouseForm
from .models import BusinessMembership
from .permissions import BUSINESS_SETTINGS, TEAM_MANAGE
from .services import (
    BusinessServiceError,
    add_warehouse,
    complete_onboarding,
    create_business_for_owner,
    update_business_profile,
)

logger = logging.getLogger(__name__)


@business_login_required
def post_login(request: HttpRequest) -> HttpResponse:
    if not request.user_memberships:
        return redirect("businesses:onboarding_start")
    business = request.business
    if business and not business.is_onboarded:
        return redirect("businesses:onboarding_start")
    return redirect("businesses:dashboard")


@business_login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    if not request.business:
        return redirect("businesses:onboarding_start")
    from apps.inventory.models import InventoryLot

    warehouses = request.business.warehouses.filter(is_active=True)
    lots = InventoryLot.objects.filter(business=request.business, archived_at__isnull=True)
    context = {
        "warehouses": warehouses,
        "warehouse_count": warehouses.count(),
        "team_count": request.business.memberships.filter(status=BusinessMembership.Status.ACTIVE).count(),
        "active_lots": lots.exclude(
            status__in=[InventoryLot.Status.SOLD, InventoryLot.Status.DRAFT, InventoryLot.Status.HIDDEN]
        ).count(),
        "needs_confirmation": lots.filter(status=InventoryLot.Status.NEEDS_CONFIRMATION).count(),
    }
    return render(request, "businesses/dashboard.html", context)


@business_login_required
@require_http_methods(["GET", "POST"])
def onboarding_start(request: HttpRequest) -> HttpResponse:
    if request.business and request.business.is_onboarded:
        return redirect("businesses:dashboard")

    if request.business is None:
        form = BusinessCreateForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            try:
                business = create_business_for_owner(
                    owner=request.user,
                    name=form.cleaned_data["name"],
                    city=form.cleaned_data.get("city", ""),
                    province=form.cleaned_data.get("province", ""),
                    phone=form.cleaned_data.get("phone", ""),
                )
            except BusinessServiceError as exc:
                form.add_error(None, exc.message)
            except Exception:
                logger.exception("Business create failed")
                form.add_error(None, "ایجاد کسب‌وکار با خطا روبه‌رو شد.")
            else:
                request.session["current_business_id"] = str(business.id)
                messages.success(request, "کسب‌وکار ایجاد شد.")
                return redirect("businesses:onboarding_warehouse")
        return render(
            request,
            "businesses/onboarding_business.html",
            {"form": form, "step": 1, "total_steps": 4},
        )

    return redirect("businesses:onboarding_warehouse")


@business_login_required
@require_http_methods(["GET", "POST"])
def onboarding_warehouse(request: HttpRequest) -> HttpResponse:
    business = request.business
    if business is None:
        return redirect("businesses:onboarding_start")

    form = WarehouseForm(request.POST or None, initial={"is_default": True, "city": business.city})
    if request.method == "POST" and form.is_valid():
        try:
            add_warehouse(
                business=business,
                name=form.cleaned_data["name"],
                city=form.cleaned_data.get("city", ""),
                address=form.cleaned_data.get("address", ""),
                is_default=form.cleaned_data.get("is_default", True),
            )
        except BusinessServiceError as exc:
            form.add_error(None, exc.message)
        except Exception:
            logger.exception("Warehouse create failed during onboarding")
            form.add_error(None, "ثبت انبار با خطا روبه‌رو شد.")
        else:
            messages.success(request, "انبار ثبت شد.")
            return redirect("businesses:onboarding_profile")

    return render(
        request,
        "businesses/onboarding_warehouse.html",
        {"form": form, "step": 2, "total_steps": 4, "business": business},
    )


@business_login_required
@require_http_methods(["GET", "POST"])
def onboarding_profile(request: HttpRequest) -> HttpResponse:
    business = request.business
    if business is None:
        return redirect("businesses:onboarding_start")

    form = BusinessProfileForm(request.POST or None, instance=business)
    if request.method == "POST" and form.is_valid():
        try:
            update_business_profile(
                business=business,
                actor_membership=request.membership,
                **form.cleaned_data,
            )
        except BusinessServiceError as exc:
            form.add_error(None, exc.message)
        else:
            messages.success(request, "اطلاعات کسب‌وکار ذخیره شد.")
            return redirect("businesses:onboarding_done")

    return render(
        request,
        "businesses/onboarding_profile.html",
        {"form": form, "step": 3, "total_steps": 4, "business": business},
    )


@business_login_required
@require_http_methods(["GET", "POST"])
def onboarding_done(request: HttpRequest) -> HttpResponse:
    business = request.business
    if business is None:
        return redirect("businesses:onboarding_start")

    if request.method == "POST":
        complete_onboarding(business)
        messages.success(request, "راه‌اندازی اولیه تمام شد.")
        return redirect("businesses:dashboard")

    return render(
        request,
        "businesses/onboarding_done.html",
        {
            "step": 4,
            "total_steps": 4,
            "business": business,
            "warehouse_count": business.warehouses.count(),
        },
    )


@business_login_required
@require_capability(BUSINESS_SETTINGS)
@require_http_methods(["GET", "POST"])
def settings_view(request: HttpRequest) -> HttpResponse:
    business = request.business
    form = BusinessProfileForm(request.POST or None, instance=business)
    if request.method == "POST" and form.is_valid():
        try:
            update_business_profile(
                business=business,
                actor_membership=request.membership,
                **form.cleaned_data,
            )
        except BusinessServiceError as exc:
            form.add_error(None, exc.message)
        else:
            messages.success(request, "تنظیمات ذخیره شد.")
            return redirect("businesses:settings")
    return render(request, "businesses/settings.html", {"form": form})


@business_login_required
@require_capability(BUSINESS_SETTINGS)
@require_http_methods(["GET", "POST"])
def warehouse_list(request: HttpRequest) -> HttpResponse:
    business = request.business
    form = WarehouseForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            add_warehouse(
                business=business,
                name=form.cleaned_data["name"],
                city=form.cleaned_data.get("city", ""),
                address=form.cleaned_data.get("address", ""),
                is_default=form.cleaned_data.get("is_default", False),
            )
        except BusinessServiceError as exc:
            form.add_error(None, exc.message)
        else:
            messages.success(request, "انبار اضافه شد.")
            return redirect("businesses:warehouses")
    warehouses = business.warehouses.all()
    return render(request, "businesses/warehouses.html", {"form": form, "warehouses": warehouses})


@business_login_required
@require_capability(TEAM_MANAGE)
def team_list(request: HttpRequest) -> HttpResponse:
    members = request.business.memberships.select_related("user").order_by("joined_at")
    return render(request, "businesses/team.html", {"members": members})


@business_login_required
@require_http_methods(["POST"])
def switch_business(request: HttpRequest) -> HttpResponse:
    business_id = request.POST.get("business_id")
    membership = (
        BusinessMembership.objects.filter(
            user=request.user,
            business_id=business_id,
            status=BusinessMembership.Status.ACTIVE,
        )
        .select_related("business")
        .first()
    )
    if membership is None:
        messages.error(request, "دسترسی به این کسب‌وکار وجود ندارد.")
        return redirect("businesses:dashboard")
    request.session["current_business_id"] = str(membership.business_id)
    messages.success(request, f"کسب‌وکار فعال: {membership.business.name}")
    return redirect("businesses:dashboard")
