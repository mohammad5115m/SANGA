from __future__ import annotations

import logging

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from apps.businesses.decorators import business_login_required, require_capability
from apps.businesses.models import Business
from apps.businesses.permissions import PARTNERS_MANAGE
from apps.marketplace.selectors import supplier_directory
from apps.notifications.models import Notification
from apps.partners.models import PartnerRelation, SupplierFollow

from .forms import PartnerRequestForm
from .selectors import incoming_requests, outgoing_relations
from .services import PartnerError, decide_partnership, request_partnership

logger = logging.getLogger(__name__)


@business_login_required
def directory(request: HttpRequest) -> HttpResponse:
    if not request.business:
        return redirect("businesses:onboarding_start")
    businesses = supplier_directory(request.business)
    outgoing = {
        str(r.supplier_business_id): r
        for r in outgoing_relations(request.business)
    }
    followed = set(
        str(x)
        for x in SupplierFollow.objects.filter(follower_business=request.business).values_list(
            "supplier_business_id", flat=True
        )
    )
    rows = []
    for biz in businesses[:100]:
        rows.append(
            {
                "business": biz,
                "relation": outgoing.get(str(biz.id)),
                "is_following": str(biz.id) in followed,
            }
        )
    return render(request, "partners/directory.html", {"rows": rows})


@business_login_required
@require_http_methods(["GET", "POST"])
def request_partner(request: HttpRequest, supplier_id) -> HttpResponse:
    if not request.business:
        return redirect("businesses:onboarding_start")
    supplier = get_object_or_404(Business, pk=supplier_id, status=Business.Status.ACTIVE)
    form = PartnerRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            request_partnership(
                partner_business=request.business,
                supplier_business=supplier,
                membership=request.membership,
                message=form.cleaned_data.get("message", ""),
            )
        except PartnerError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(request, "درخواست همکاری ارسال شد.")
            return redirect("partners:directory")
    return render(
        request,
        "partners/request.html",
        {"form": form, "supplier": supplier},
    )


@business_login_required
@require_capability(PARTNERS_MANAGE)
def incoming(request: HttpRequest) -> HttpResponse:
    relations = incoming_requests(request.business)
    approved = PartnerRelation.objects.filter(
        supplier_business=request.business,
        status=PartnerRelation.Status.APPROVED,
    ).select_related("partner_business")
    return render(
        request,
        "partners/incoming.html",
        {"relations": relations, "approved": approved},
    )


@business_login_required
@require_capability(PARTNERS_MANAGE)
@require_POST
def decide(request: HttpRequest, relation_id) -> HttpResponse:
    relation = get_object_or_404(
        PartnerRelation,
        pk=relation_id,
        supplier_business=request.business,
    )
    approve = request.POST.get("decision") == "approve"
    try:
        decide_partnership(relation=relation, membership=request.membership, approve=approve)
    except PartnerError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "تصمیم ثبت شد.")
    return redirect("partners:incoming")


@business_login_required
def notifications_list(request: HttpRequest) -> HttpResponse:
    notes = Notification.objects.filter(user=request.user).order_by("-created_at")[:50]
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    return render(request, "partners/notifications.html", {"notifications": notes})
