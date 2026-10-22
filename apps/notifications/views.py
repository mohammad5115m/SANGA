from urllib.parse import urlencode

from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from apps.businesses.decorators import business_login_required
from apps.core.pagination import paginate

from .selectors import (
    notification_feed,
    notification_rows,
    unread_notification_count,
)
from .services import mark_all_notifications_read, mark_notification_read


@business_login_required
@require_GET
def notifications_list(request: HttpRequest) -> HttpResponse:
    state = "unread" if request.GET.get("state") == "unread" else "all"
    page = paginate(request, notification_feed(request, unread_only=state == "unread"))
    return render(request, "notifications/list.html", {
        "notifications": notification_rows(request, page.object_list), "page": page,
        "state": state, "unread_count": unread_notification_count(request),
    })


def _return_to_list(request):
    params = {}
    if request.POST.get("state") == "unread":
        params["state"] = "unread"
    if request.POST.get("page", "").isdigit():
        params["page"] = request.POST["page"][:8]
    url = reverse("notifications:list")
    return redirect(f"{url}?{urlencode(params)}" if params else url)


@business_login_required
@require_POST
def notification_read(request, source, notification_id):
    if not mark_notification_read(request, source=source, notification_id=notification_id):
        raise Http404
    messages.success(request, "اعلان خوانده‌شده ثبت شد.")
    return _return_to_list(request)


@business_login_required
@require_POST
def notifications_read_all(request):
    mark_all_notifications_read(request)
    messages.success(request, "اعلان‌های این کسب‌وکار و اعلان‌های شخصی شما خوانده‌شده ثبت شدند.")
    return _return_to_list(request)
