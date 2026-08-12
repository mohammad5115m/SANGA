from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from apps.businesses.decorators import business_login_required

from .models import Notification


@business_login_required
def notifications_list(request: HttpRequest) -> HttpResponse:
    notes = Notification.objects.filter(user=request.user).order_by("-created_at")[:50]
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    return render(request, "notifications/list.html", {"notifications": notes})
