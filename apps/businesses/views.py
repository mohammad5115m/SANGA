from __future__ import annotations

import logging

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .dashboard import dashboard_data
from .decorators import business_login_required, require_capability
from .directory import (
    colleague_businesses,
    filter_colleagues,
    get_colleague,
    representative_of,
)
from .entitlements import entitlements_for, seats_remaining
from .forms import BusinessProfileForm
from .models import BusinessMembership
from .permissions import (
    BUSINESS_SETTINGS,
    INVOICE_VIEW,
    LEDGER_VIEW,
    TEAM_MANAGE,
    TRADE_CONFIRM,
    TRADE_PROPOSE,
    label_for,
)
from .services import BusinessServiceError, complete_onboarding, update_business_profile

logger = logging.getLogger(__name__)

COLLEAGUE_ROWS = 60


@business_login_required
def post_login(request: HttpRequest) -> HttpResponse:
    if not request.user_memberships:
        return redirect("businesses:no_business")
    business = request.business
    if business and not business.is_onboarded:
        return redirect("businesses:onboarding_profile")
    return redirect("businesses:dashboard")


@business_login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    if not request.business:
        return redirect("businesses:no_business")
    context = dashboard_data(business=request.business, membership=request.membership)
    return render(request, "businesses/dashboard.html", context)


@business_login_required
@require_http_methods(["GET"])
def more(request: HttpRequest) -> HttpResponse:
    """«بیشتر» — the screens visited weekly rather than hourly.

    A hub rather than a sixth top-level tab for each: keeping the primary bar to
    six items is what makes it readable on a phone.
    """
    if not request.business:
        return redirect("businesses:no_business")
    return render(request, "businesses/more.html")


@business_login_required
@require_http_methods(["GET"])
def no_business(request: HttpRequest) -> HttpResponse:
    """Dead end for a User who belongs to no Business.

    Businesses are provisioned by a Platform Admin, so there is deliberately no
    form here. A User reaching this page is either mid-provisioning or has had
    every membership suspended.
    """
    if request.business is not None:
        return redirect("businesses:dashboard")
    return render(request, "businesses/no_business.html")


@business_login_required
@require_http_methods(["GET", "POST"])
def onboarding_profile(request: HttpRequest) -> HttpResponse:
    business = request.business
    if business is None:
        return redirect("businesses:no_business")

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
        {"form": form, "step": 2, "total_steps": 3, "business": business},
    )


@business_login_required
@require_http_methods(["GET", "POST"])
def onboarding_done(request: HttpRequest) -> HttpResponse:
    business = request.business
    if business is None:
        return redirect("businesses:no_business")

    if request.method == "POST":
        complete_onboarding(business)
        messages.success(request, "راه‌اندازی اولیه تمام شد.")
        return redirect("businesses:dashboard")

    return render(
        request,
        "businesses/onboarding_done.html",
        {
            "step": 3,
            "total_steps": 3,
            "business": business,
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
@require_capability(TEAM_MANAGE)
def team_list(request: HttpRequest) -> HttpResponse:
    members = request.business.memberships.select_related("user").order_by("joined_at")
    return render(
        request,
        "businesses/team.html",
        {
            "members": members,
            "seats_remaining": seats_remaining(request.business),
            "seat_limit": request.business.seat_limit,
            "capability_label": label_for,
        },
    )


# --- colleague directory ------------------------------------------------------


@business_login_required
def colleague_list(request: HttpRequest) -> HttpResponse:
    """«لیست همکاران» — every eligible Business, no manual entry required."""
    if not request.business:
        return redirect("businesses:no_business")

    q = request.GET.get("q", "").strip()
    qs = filter_colleagues(colleague_businesses(request.business), q=q)
    return render(
        request,
        "businesses/colleague_list.html",
        {"colleagues": qs[:COLLEAGUE_ROWS], "q": q},
    )


@business_login_required
def colleague_detail(request: HttpRequest, business_id) -> HttpResponse:
    if not request.business:
        return redirect("businesses:no_business")

    colleague = get_colleague(request.business, business_id)
    colleague_is_current = colleague is not None
    if colleague is None:
        from apps.accounting.selectors import accounting_counterparty

        colleague = accounting_counterparty(request.business, business_id)
    if colleague is None:
        messages.error(request, "این همکار در دسترس نیست.")
        return redirect("businesses:colleagues")

    from apps.inventory.policy import eligible_items

    items = (
        eligible_items(
            audience="colleague",
            viewer_business=request.business,
            seller_business=colleague,
        )[:12]
        if colleague_is_current
        else []
    )

    can_view_ledger = request.membership.has_capability(LEDGER_VIEW)
    can_view_invoices = request.membership.has_capability(INVOICE_VIEW)
    balance = None
    ledger_entries = []
    invoices = []
    if can_view_ledger:
        from apps.accounting.selectors import counterparty_statement, current_balance, describe_balance

        balance = describe_balance(current_balance(request.business, colleague))
        ledger_entries = counterparty_statement(request.business, colleague).order_by("-created_at")[:8]
    if can_view_invoices:
        from apps.invoicing.selectors import invoices_between

        invoices = invoices_between(request.business, colleague)[:8]

    can_view_trades = request.membership.has_capability(
        TRADE_PROPOSE
    ) or request.membership.has_capability(TRADE_CONFIRM)
    if can_view_trades:
        from apps.trading.selectors import proposals_between, trades_between

        proposals = proposals_between(request.business, colleague)[:8]
        trades = trades_between(request.business, colleague)[:8]
    else:
        proposals = []
        trades = []

    context = {
        "colleague": colleague,
        "representative": representative_of(colleague),
        "items": items,
        "can_view_ledger": can_view_ledger,
        "can_view_invoices": can_view_invoices,
        "balance": balance,
        "ledger_entries": ledger_entries,
        "invoices": invoices,
        "proposals": proposals,
        "trades": trades,
        "can_view_trades": can_view_trades,
        "can_propose": colleague_is_current
        and request.membership.has_capability(TRADE_PROPOSE),
        "colleague_is_current": colleague_is_current,
        "colleague_entitlements": entitlements_for(colleague),
    }
    return render(request, "businesses/colleague_detail.html", context)


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
