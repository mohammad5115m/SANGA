from __future__ import annotations

import logging

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.businesses.decorators import business_login_required, require_capability
from apps.businesses.permissions import LEADS_MANAGE, LEADS_VIEW
from apps.core.pagination import ROW_PAGE_SIZE, paginate

from .crm import (
    CATEGORY_CHOICES,
    CUSTOMER_STATUS_CHOICES,
    FOLLOWUP_FILTERS,
    CRMRepository,
    crm_mode_notice,
)
from .forms import (
    CompleteFollowUpForm,
    CustomerNoteForm,
    CustomerProfileForm,
    FollowUpForm,
    RescheduleFollowUpForm,
)
from .models import Inquiry
from .selectors import (
    filter_inquiries,
    get_inquiry,
    inquiries_for,
)
from .services import InquiryError, mark_inquiry_viewed, set_inquiry_status

logger = logging.getLogger(__name__)

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
    page = paginate(request, qs, per_page=ROW_PAGE_SIZE)
    return render(
        request,
        "inquiries/inbox.html",
        {
            "inquiries": page.object_list,
            "page": page,
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
        messages.error(request, "درخواست خرید یافت نشد.")
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
        messages.error(request, "درخواست خرید یافت نشد.")
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
        messages.success(request, "وضعیت درخواست خرید به‌روزرسانی شد.")
    return redirect("inquiries:detail", inquiry_id=inquiry.id)


@business_login_required
@require_capability(LEADS_VIEW)
def lead_list(request: HttpRequest) -> HttpResponse:
    q = request.GET.get("q", "").strip()
    category = request.GET.get("category", "")
    followup_state = request.GET.get("followup", "")
    crm_status = request.GET.get("status", "")
    repository = CRMRepository(request)
    customers = repository.list_customers(
        q=q,
        category=category,
        followup_state=followup_state,
        status=crm_status,
    )
    page = paginate(request, customers, per_page=ROW_PAGE_SIZE)
    return render(
        request,
        "inquiries/lead_list.html",
        {
            "leads": page.object_list,
            "page": page,
            "q": q,
            "category": category,
            "followup_state": followup_state,
            "crm_status": crm_status,
            "category_choices": CATEGORY_CHOICES,
            "followup_filters": FOLLOWUP_FILTERS,
            "customer_status_choices": CUSTOMER_STATUS_CHOICES,
            "crm_notice": crm_mode_notice(),
            "can_manage": request.membership.has_capability(LEADS_MANAGE),
        },
    )


@business_login_required
@require_capability(LEADS_VIEW)
def lead_detail(request: HttpRequest, lead_id) -> HttpResponse:
    repository = CRMRepository(request)
    lead = repository.get_customer(lead_id)
    if lead is None:
        messages.error(request, "مشتری یافت نشد.")
        return redirect("inquiries:leads")
    related_context = "، ".join(lead["requested_products"][:2])
    return render(
        request,
        "inquiries/lead_detail.html",
        {
            "lead": lead,
            "inquiries": lead["inquiries"],
            "note_form": CustomerNoteForm(),
            "profile_form": CustomerProfileForm(
                initial={
                    "category": lead["category"],
                    "crm_status": lead["crm_status"],
                    "tags": "، ".join(lead["tags"]),
                    "current_needs": lead["current_needs"],
                }
            ),
            "followup_form": FollowUpForm(initial={"related_context": related_context}),
            "complete_form": CompleteFollowUpForm(),
            "crm_notice": crm_mode_notice(),
            "can_manage": request.membership.has_capability(LEADS_MANAGE),
        },
    )


@business_login_required
@require_capability(LEADS_VIEW)
def followup_list(request: HttpRequest) -> HttpResponse:
    state = request.GET.get("state", "")
    repository = CRMRepository(request)
    all_followups = repository.list_followups()
    counts = {
        key: sum(item["bucket"] == key for item in all_followups)
        for key in ("overdue", "today", "upcoming", "completed")
    }
    return render(
        request,
        "inquiries/followup_list.html",
        {
            "groups": repository.followup_groups() if not state else [],
            "followups": repository.list_followups(state=state) if state else [],
            "state": state,
            "followup_filters": FOLLOWUP_FILTERS,
            "counts": counts,
            "crm_notice": crm_mode_notice(),
            "can_manage": request.membership.has_capability(LEADS_MANAGE),
        },
    )


@business_login_required
@require_capability(LEADS_MANAGE)
@require_POST
def lead_add_note(request: HttpRequest, lead_id) -> HttpResponse:
    form = CustomerNoteForm(request.POST)
    if form.is_valid():
        try:
            CRMRepository(request).add_note(lead_id, form.cleaned_data["text"])
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "یادداشت به پرونده مشتری اضافه شد.")
    else:
        messages.error(request, "متن یادداشت را بررسی کنید.")
    return redirect("inquiries:lead_detail", lead_id=lead_id)


@business_login_required
@require_capability(LEADS_MANAGE)
@require_POST
def lead_update_profile(request: HttpRequest, lead_id) -> HttpResponse:
    form = CustomerProfileForm(request.POST)
    if form.is_valid():
        raw_tags = form.cleaned_data["tags"].replace("،", ",")
        try:
            CRMRepository(request).update_customer(
                lead_id,
                category=form.cleaned_data["category"],
                crm_status=form.cleaned_data["crm_status"],
                tags=raw_tags.split(","),
                current_needs=form.cleaned_data["current_needs"],
            )
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "دسته‌بندی و نیاز مشتری به‌روزرسانی شد.")
    else:
        messages.error(request, "اطلاعات پرونده مشتری را بررسی کنید.")
    return redirect("inquiries:lead_detail", lead_id=lead_id)


@business_login_required
@require_capability(LEADS_MANAGE)
@require_POST
def lead_schedule_followup(request: HttpRequest, lead_id) -> HttpResponse:
    form = FollowUpForm(request.POST)
    if form.is_valid():
        try:
            CRMRepository(request).schedule_followup(
                lead_id,
                title=form.cleaned_data["title"],
                scheduled_for=form.cleaned_data["scheduled_for"],
                reminder_minutes=form.cleaned_data["reminder_minutes"],
                priority=form.cleaned_data["priority"],
                note=form.cleaned_data["note"],
                related_context=form.cleaned_data["related_context"],
            )
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "پیگیری بعدی زمان‌بندی شد.")
    else:
        messages.error(request, "اطلاعات زمان‌بندی کامل یا معتبر نیست.")
    return redirect("inquiries:lead_detail", lead_id=lead_id)


@business_login_required
@require_capability(LEADS_MANAGE)
@require_POST
def lead_record_followup(request: HttpRequest, lead_id) -> HttpResponse:
    form = CompleteFollowUpForm(request.POST)
    if form.is_valid():
        try:
            CRMRepository(request).complete_customer_followup(
                lead_id, note=form.cleaned_data["note"]
            )
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "پیگیری انجام‌شده در سابقه مشتری ثبت شد.")
    return redirect("inquiries:lead_detail", lead_id=lead_id)


@business_login_required
@require_capability(LEADS_MANAGE)
@require_POST
def followup_complete(request: HttpRequest, followup_id) -> HttpResponse:
    form = CompleteFollowUpForm(request.POST)
    if form.is_valid():
        try:
            CRMRepository(request).complete_followup(
                followup_id, note=form.cleaned_data["note"]
            )
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "پیگیری انجام‌شده ثبت شد.")
    return redirect("inquiries:followups")


@business_login_required
@require_capability(LEADS_MANAGE)
@require_POST
def followup_postpone(request: HttpRequest, followup_id) -> HttpResponse:
    try:
        CRMRepository(request).postpone_followup(followup_id)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "پیگیری یک روز به تعویق افتاد.")
    return redirect("inquiries:followups")


@business_login_required
@require_capability(LEADS_MANAGE)
@require_POST
def followup_reschedule(request: HttpRequest, followup_id) -> HttpResponse:
    form = RescheduleFollowUpForm(request.POST)
    if form.is_valid():
        try:
            CRMRepository(request).reschedule_followup(
                followup_id, form.cleaned_data["scheduled_for"]
            )
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "زمان پیگیری به‌روزرسانی شد.")
    else:
        messages.error(request, "زمان جدید معتبر نیست.")
    return redirect("inquiries:followups")


@business_login_required
@require_capability(LEADS_MANAGE)
@require_POST
def followup_cancel(request: HttpRequest, followup_id) -> HttpResponse:
    try:
        CRMRepository(request).cancel_followup(followup_id)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "پیگیری لغو شد و در سابقه باقی ماند.")
    return redirect("inquiries:followups")
