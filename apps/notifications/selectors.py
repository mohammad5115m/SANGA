"""One tenant-scoped, paginated feed for stored notifications and CRM reminders."""

from django.db.models import Exists, F, OuterRef, Q, Value
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from apps.businesses.permissions import LEADS_MANAGE
from apps.inquiries.models import CustomerFollowUp, FollowUpReminderRead

from .models import Notification


def stored_notifications(request):
    return Notification.objects.filter(user=request.user).filter(
        Q(business=request.business) | Q(business__isnull=True),
    )


def reminder_notifications(request):
    membership = getattr(request, "membership", None)
    if membership is None or not membership.has_capability(LEADS_MANAGE):
        return CustomerFollowUp.objects.none()
    return CustomerFollowUp.objects.filter(
        business=request.business,
        status__in=(CustomerFollowUp.Status.SCHEDULED, CustomerFollowUp.Status.POSTPONED),
        remind_at__lte=timezone.now(),
    )


def notification_feed(request, *, unread_only=False):
    stored = stored_notifications(request)
    reminders = reminder_notifications(request).annotate(
        is_read=Exists(FollowUpReminderRead.objects.filter(followup=OuterRef("pk"), user=request.user)),
    )
    if unread_only:
        stored = stored.filter(is_read=False)
        reminders = reminders.filter(is_read=False)
    columns = ("id", "occurred_at", "is_read", "source")
    stored = stored.order_by().annotate(occurred_at=F("created_at"), source=Value("stored")).values(*columns)
    reminders = reminders.order_by().annotate(occurred_at=F("remind_at"), source=Value("reminder")).values(*columns)
    return stored.union(reminders, all=True).order_by("-occurred_at", "source", "id")


def unread_notification_count(request):
    if not hasattr(request, "_notification_unread_count"):
        request._notification_unread_count = notification_feed(request, unread_only=True).order_by().count()
    return request._notification_unread_count


def notification_rows(request, entries):
    """Load details only for the current page, in two bounded queries."""
    stored = {
        row.pk: row for row in stored_notifications(request).filter(
            pk__in=[entry["id"] for entry in entries if entry["source"] == "stored"],
        )
    }
    reminders = {
        row.pk: row for row in reminder_notifications(request).filter(
            pk__in=[entry["id"] for entry in entries if entry["source"] == "reminder"],
        ).select_related("customer")
    }
    rows = []
    for entry in entries:
        source = entry["source"]
        obj = (stored if source == "stored" else reminders).get(entry["id"])
        if obj is None:
            continue
        if source == "stored":
            title, body, link, kind = obj.title, obj.body, obj.link, obj.get_kind_display()
        else:
            title, body = f"پیگیری {obj.customer.name}", obj.title
            link, kind = reverse("inquiries:lead_detail", args=[obj.customer_id]), "یادآوری پیگیری"
        safe_link = link if link.startswith("/") and url_has_allowed_host_and_scheme(
            link, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
        ) else ""
        rows.append({
            "id": obj.pk, "source": source, "title": title, "body": body,
            "link": safe_link, "is_read": entry["is_read"], "created_at": entry["occurred_at"],
            "kind_label": kind, "scheduled_for": obj.scheduled_for if source == "reminder" else None,
        })
    return rows
