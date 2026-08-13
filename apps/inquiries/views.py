from __future__ import annotations

import logging

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.businesses.decorators import business_login_required, require_capability
from apps.businesses.permissions import LEADS_MANAGE, LEADS_VIEW

from .models import Inquiry
from .selectors import (
    filter_inquiries,
    filter_leads,
    get_inquiry,
    get_lead,
    inquiries_for,
    leads_for,
)
from .services import InquiryError, mark_inquiry_viewed, set_inquiry_status

logger = logging.getLogger(__name__)

ROWS = 60

STATUS_FILTERS = (
    ("", "همه"),
    ("open", "در جریان"),
    (Inquiry.Status.NEW, "جدید"),
    (Inquiry.Status.CONTACTED, "تماس گرفته‌شده"),
    (Inquiry.Status.CONVERTED, "تبدیل به فروش"),
    (Inquiry.Status.CLOSED, "بسته"),
)


@business_login_required
@require_capability(LEADS_VIEW)
def inquiry_inbox(request: HttpRequest) -> HttpResponse:
    status = request.GET.get("status", "")
    q = request.GET.get("q", "").strip()
    qs = filter_inquiries(inquiries_for(request.business), status=status, q=q)
    return render(
        request,
        "inquiries/inbox.html",
        {
            "inquiries": qs[:ROWS],
            "status": status,
            "q": q,
            "status_filters": STATUS_FILTERS,
            "can_manage": request.membership.has_capability(LEADS_MANAGE),
        },
    )


@business_login_required
@require_capability(LEADS_VIEW)
def inquiry_detail(request: HttpRequest, inquiry_id) -> HttpResponse:
    inquiry = get_inquiry(request.business, inquiry_id)
    if inquiry is None:
        messages.error(request, "استعلام یافت نشد.")
        return redirect("inquiries:inbox")
    mark_inquiry_viewed(inquiry=inquiry)
    return render(
        request,
        "inquiries/detail.html",
        {
            "inquiry": inquiry,
            "status_choices": Inquiry.Status.choices,
            "can_manage": request.membership.has_capability(LEADS_MANAGE),
        },
    )


@business_login_required
@require_capability(LEADS_MANAGE)
@require_POST
def inquiry_set_status(request: HttpRequest, inquiry_id) -> HttpResponse:
    inquiry = get_inquiry(request.business, inquiry_id)
    if inquiry is None:
        messages.error(request, "استعلام یافت نشد.")
        return redirect("inquiries:inbox")
    try:
        set_inquiry_status(
            inquiry=inquiry,
            status=request.POST.get("status", ""),
            membership=request.membership,
        )
    except InquiryError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "وضعیت استعلام به‌روزرسانی شد.")
    return redirect("inquiries:detail", inquiry_id=inquiry.id)


@business_login_required
@require_capability(LEADS_VIEW)
def lead_list(request: HttpRequest) -> HttpResponse:
    q = request.GET.get("q", "").strip()
    return render(
        request,
        "inquiries/lead_list.html",
        {"leads": filter_leads(leads_for(request.business), q=q)[:ROWS], "q": q},
    )


@business_login_required
@require_capability(LEADS_VIEW)
def lead_detail(request: HttpRequest, lead_id) -> HttpResponse:
    lead = get_lead(request.business, lead_id)
    if lead is None:
        messages.error(request, "مشتری یافت نشد.")
        return redirect("inquiries:leads")
    return render(
        request,
        "inquiries/lead_detail.html",
        {
            "lead": lead,
            "inquiries": lead.inquiries.prefetch_related("items").order_by("-created_at"),
        },
    )
