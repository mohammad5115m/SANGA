from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from apps.businesses.decorators import business_login_required
from apps.businesses.permissions import LEADS_MANAGE
from apps.inquiries.crm import CRMRepository

from .models import Notification


@business_login_required
def notifications_list(request: HttpRequest) -> HttpResponse:
    stored_notes = Notification.objects.filter(user=request.user).order_by("-created_at")[:50]
    notes = [
        {
            "id": str(note.pk),
            "title": note.title,
            "body": note.body,
            "link": note.link,
            "is_read": note.is_read,
            "created_at": note.created_at,
            "kind_label": note.get_kind_display(),
        }
        for note in stored_notes
    ]
    crm_reminders = []
    membership = getattr(request, "membership", None)
    if membership is not None and membership.has_capability(LEADS_MANAGE):
        repository = CRMRepository(request)
        crm_reminders = repository.reminder_notifications()
        notes.extend(crm_reminders)
        repository.mark_reminders_read([item["reminder_id"] for item in crm_reminders])
    notes.sort(key=lambda note: note["created_at"], reverse=True)
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    return render(request, "notifications/list.html", {"notifications": notes})
