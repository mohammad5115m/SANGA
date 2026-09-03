"""Persistent, tenant-safe CRM repository.

Views and templates consume dictionaries from this boundary instead of ORM
objects directly. Storage is operational and backed by Django models while the
presentation contract stays stable.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Exists, OuterRef, Prefetch
from django.urls import reverse
from django.utils import timezone

from apps.core.persian import normalize_persian_text

from .models import (
    CustomerFollowUp,
    CustomerLead,
    CustomerNote,
    FollowUpReminderRead,
    Inquiry,
)

CATEGORY_CHOICES = CustomerLead.Category.choices
CATEGORY_LABELS = dict(CATEGORY_CHOICES)
CUSTOMER_STATUS_CHOICES = CustomerLead.CRMStatus.choices
CUSTOMER_STATUS_LABELS = dict(CUSTOMER_STATUS_CHOICES)
FOLLOWUP_STATUS_CHOICES = CustomerFollowUp.Status.choices
FOLLOWUP_STATUS_LABELS = dict(FOLLOWUP_STATUS_CHOICES)
PRIORITY_CHOICES = CustomerFollowUp.Priority.choices
PRIORITY_LABELS = dict(PRIORITY_CHOICES)

FOLLOWUP_FILTERS = (
    ("", "همه"),
    ("overdue", "عقب‌افتاده"),
    ("today", "امروز"),
    ("upcoming", "آینده"),
    ("completed", "انجام‌شده"),
)


class CRMRepository:
    """Operational CRM storage scoped to the current Business and user."""

    def __init__(self, request) -> None:
        self.request = request
        self.business = request.business
        self.user = request.user
        self._followup_cache: list[dict] | None = None

    def list_customers(
        self,
        *,
        q: str = "",
        category: str = "",
        followup_state: str = "",
        status: str = "",
    ) -> list[dict]:
        customers = [self._decorate_customer(item) for item in self._real_customers()]
        term = normalize_persian_text(q).casefold()
        if term:
            customers = [item for item in customers if term in self._customer_search_text(item)]
        if category:
            customers = [item for item in customers if item["category"] == category]
        if followup_state:
            customers = [item for item in customers if item["followup_state"] == followup_state]
        if status:
            customers = [item for item in customers if item["crm_status"] == status]
        return sorted(
            customers,
            key=lambda item: (
                self._followup_rank(item["followup_state"]),
                -(item["last_activity_at"].timestamp() if item["last_activity_at"] else 0),
            ),
        )

    def get_customer(self, customer_id) -> dict | None:
        lead = self._lead_queryset().filter(pk=customer_id).first()
        return self._decorate_customer(self._customer_record(lead)) if lead else None

    def list_followups(self, *, state: str = "") -> list[dict]:
        followups = self._loaded_followups()
        return [item for item in followups if item["bucket"] == state] if state else followups

    def followup_groups(self) -> list[dict]:
        followups = self._loaded_followups()
        definitions = (
            ("overdue", "عقب‌افتاده", "این تماس‌ها از زمان برنامه‌ریزی‌شده گذشته‌اند."),
            ("today", "امروز", "کارهایی که بهتر است امروز انجام شوند."),
            ("upcoming", "آینده", "پیگیری‌های برنامه‌ریزی‌شده روزهای بعد."),
            ("completed", "انجام‌شده", "پیگیری‌های تکمیل یا لغوشده."),
        )
        return [
            {
                "key": key,
                "label": label,
                "description": description,
                "items": [item for item in followups if item["bucket"] == key],
            }
            for key, label, description in definitions
        ]

    def reminder_notifications(self) -> list[dict]:
        read_for_user = FollowUpReminderRead.objects.filter(
            followup_id=OuterRef("pk"), user=self.user
        )
        followups = (
            CustomerFollowUp.objects.filter(
                business=self.business,
                status__in=(
                    CustomerFollowUp.Status.SCHEDULED,
                    CustomerFollowUp.Status.POSTPONED,
                ),
                remind_at__lte=timezone.now(),
            )
            .select_related("customer")
            .annotate(reminder_is_read=Exists(read_for_user))
            .order_by("-remind_at")
        )
        return [
            {
                "id": f"crm-{followup.pk}",
                "reminder_id": str(followup.pk),
                "title": f"پیگیری {followup.customer.name}",
                "body": f"{followup.title} · {self._due_label(followup.scheduled_for, followup.status)}",
                "link": reverse(
                    "inquiries:lead_detail", kwargs={"lead_id": followup.customer_id}
                ),
                "is_read": followup.reminder_is_read,
                "created_at": followup.remind_at,
                "kind_label": "یادآوری پیگیری",
            }
            for followup in followups
        ]

    def unread_reminder_count(self) -> int:
        return (
            CustomerFollowUp.objects.filter(
                business=self.business,
                status__in=(
                    CustomerFollowUp.Status.SCHEDULED,
                    CustomerFollowUp.Status.POSTPONED,
                ),
                remind_at__lte=timezone.now(),
            )
            .exclude(reminder_reads__user=self.user)
            .count()
        )

    def add_note(self, customer_id, text: str) -> None:
        customer = self._get_lead(customer_id)
        value = (text or "").strip()
        if not value:
            raise ValueError("متن یادداشت را وارد کنید.")
        CustomerNote.objects.create(
            business=self.business,
            customer=customer,
            author=self.user,
            text=value[:1000],
        )

    def update_customer(
        self,
        customer_id,
        *,
        category: str,
        crm_status: str,
        tags: list[str],
        current_needs: str,
    ) -> None:
        customer = self._get_lead(customer_id)
        customer.category = category if category in CATEGORY_LABELS else CustomerLead.Category.OTHER
        customer.crm_status = (
            crm_status if crm_status in CUSTOMER_STATUS_LABELS else CustomerLead.CRMStatus.ACTIVE
        )
        customer.tags = [value.strip()[:60] for value in tags if value.strip()][:12]
        customer.current_needs = (current_needs or "").strip()[:1000]
        customer.save(
            update_fields=["category", "crm_status", "tags", "current_needs", "updated_at"]
        )

    def schedule_followup(
        self,
        customer_id,
        *,
        title: str,
        scheduled_for: datetime,
        reminder_minutes: int,
        priority: str,
        note: str = "",
        related_context: str = "",
    ) -> str:
        customer = self._get_lead(customer_id)
        title = (title or "").strip()
        if not title:
            raise ValueError("موضوع پیگیری را وارد کنید.")
        scheduled_for = self._aware(scheduled_for)
        reminder_minutes = self._valid_reminder_minutes(reminder_minutes)
        followup = CustomerFollowUp.objects.create(
            business=self.business,
            customer=customer,
            created_by=self.user,
            title=title[:160],
            scheduled_for=scheduled_for,
            reminder_minutes=reminder_minutes,
            remind_at=scheduled_for - timedelta(minutes=reminder_minutes),
            priority=(
                priority if priority in PRIORITY_LABELS else CustomerFollowUp.Priority.NORMAL
            ),
            status=CustomerFollowUp.Status.SCHEDULED,
            note=(note or "").strip()[:1000],
            related_context=(related_context or "").strip()[:255],
        )
        self._followup_cache = None
        return str(followup.pk)

    def complete_followup(self, followup_id, *, note: str = "") -> None:
        with transaction.atomic():
            followup = self._locked_followup(followup_id)
            followup.status = CustomerFollowUp.Status.COMPLETED
            followup.completed_at = timezone.now()
            if note.strip():
                followup.note = note.strip()[:1000]
            followup.save(update_fields=["status", "completed_at", "note", "updated_at"])
        self._followup_cache = None

    def complete_customer_followup(self, customer_id, *, note: str = "") -> None:
        customer = self._get_lead(customer_id)
        now = timezone.now()
        with transaction.atomic():
            followup = (
                CustomerFollowUp.objects.select_for_update()
                .filter(
                    business=self.business,
                    customer=customer,
                    status__in=(
                        CustomerFollowUp.Status.SCHEDULED,
                        CustomerFollowUp.Status.POSTPONED,
                    ),
                )
                .order_by("scheduled_for")
                .first()
            )
            if followup is None:
                CustomerFollowUp.objects.create(
                    business=self.business,
                    customer=customer,
                    created_by=self.user,
                    title="پیگیری تلفنی",
                    scheduled_for=now,
                    reminder_minutes=0,
                    remind_at=now,
                    priority=CustomerFollowUp.Priority.NORMAL,
                    status=CustomerFollowUp.Status.COMPLETED,
                    note=(note or "").strip()[:1000],
                    completed_at=now,
                )
            else:
                followup.status = CustomerFollowUp.Status.COMPLETED
                followup.completed_at = now
                if note.strip():
                    followup.note = note.strip()[:1000]
                followup.save(update_fields=["status", "completed_at", "note", "updated_at"])
        self._followup_cache = None

    def postpone_followup(self, followup_id) -> None:
        with transaction.atomic():
            followup = self._locked_followup(followup_id)
            followup.scheduled_for = max(followup.scheduled_for, timezone.now()) + timedelta(
                days=1
            )
            followup.remind_at = followup.scheduled_for - timedelta(
                minutes=followup.reminder_minutes
            )
            followup.status = CustomerFollowUp.Status.POSTPONED
            followup.save(
                update_fields=["scheduled_for", "remind_at", "status", "updated_at"]
            )
            followup.reminder_reads.all().delete()
        self._followup_cache = None

    def reschedule_followup(self, followup_id, scheduled_for: datetime) -> None:
        with transaction.atomic():
            followup = self._locked_followup(followup_id)
            followup.scheduled_for = self._aware(scheduled_for)
            followup.remind_at = followup.scheduled_for - timedelta(
                minutes=followup.reminder_minutes
            )
            followup.status = CustomerFollowUp.Status.SCHEDULED
            followup.completed_at = None
            followup.save(
                update_fields=[
                    "scheduled_for",
                    "remind_at",
                    "status",
                    "completed_at",
                    "updated_at",
                ]
            )
            followup.reminder_reads.all().delete()
        self._followup_cache = None

    def cancel_followup(self, followup_id) -> None:
        with transaction.atomic():
            followup = self._locked_followup(followup_id)
            followup.status = CustomerFollowUp.Status.CANCELLED
            followup.save(update_fields=["status", "updated_at"])
        self._followup_cache = None

    def mark_reminders_read(self, reminder_ids: list[str]) -> None:
        if not reminder_ids:
            return
        valid_ids = CustomerFollowUp.objects.filter(
            business=self.business, pk__in=reminder_ids
        ).values_list("pk", flat=True)
        FollowUpReminderRead.objects.bulk_create(
            [
                FollowUpReminderRead(followup_id=followup_id, user=self.user)
                for followup_id in valid_ids
            ],
            ignore_conflicts=True,
        )

    def _lead_queryset(self):
        inquiry_qs = Inquiry.objects.prefetch_related("items").order_by("-created_at")
        return CustomerLead.objects.filter(business=self.business).prefetch_related(
            Prefetch("inquiries", queryset=inquiry_qs, to_attr="crm_inquiries"),
            Prefetch(
                "crm_notes",
                queryset=CustomerNote.objects.order_by("-created_at"),
                to_attr="crm_note_rows",
            ),
            Prefetch(
                "crm_followups",
                queryset=CustomerFollowUp.objects.order_by("scheduled_for"),
                to_attr="crm_followup_rows",
            ),
        )

    def _real_customers(self) -> list[dict]:
        return [self._customer_record(lead) for lead in self._lead_queryset()]

    def _customer_record(self, lead: CustomerLead) -> dict:
        inquiries = []
        requested_products: list[str] = []
        for inquiry in lead.crm_inquiries:
            products = [line.product_name for line in inquiry.items.all()]
            requested_products.extend(products)
            inquiries.append(
                {
                    "id": str(inquiry.pk),
                    "title": "، ".join(products) or "بدون محصول مشخص",
                    "status": inquiry.status,
                    "status_label": inquiry.get_status_display(),
                    "created_at": inquiry.created_at,
                    "url": reverse("inquiries:detail", kwargs={"inquiry_id": inquiry.pk}),
                    "message": inquiry.message,
                }
            )
        tags = lead.tags if isinstance(lead.tags, list) else []
        notes = [
            {"text": note.text, "created_at": note.created_at} for note in lead.crm_note_rows
        ]
        if lead.note:
            notes.append({"text": lead.note, "created_at": lead.created_at})
        return {
            "id": str(lead.pk),
            "name": lead.name,
            "phone": lead.phone,
            "is_verified": lead.is_verified,
            "category": lead.category,
            "tags": [str(value) for value in tags],
            "crm_status": lead.crm_status,
            "requested_products": list(dict.fromkeys(requested_products)),
            "current_needs": lead.current_needs
            or next((item["message"] for item in inquiries if item["message"]), ""),
            "notes": notes,
            "inquiries": inquiries,
            "followup_records": [
                self._followup_record(
                    followup,
                    customer_name=lead.name,
                    customer_phone=lead.phone,
                )
                for followup in lead.crm_followup_rows
            ],
            "created_at": lead.created_at,
        }

    def _decorate_customer(self, customer: dict) -> dict:
        item = dict(customer)
        notes = sorted(item["notes"], key=lambda note: note["created_at"], reverse=True)
        customer_followups = [
            self._decorate_followup(record, customer=item)
            for record in item["followup_records"]
        ]
        active = [
            followup
            for followup in customer_followups
            if followup["status"]
            in (CustomerFollowUp.Status.SCHEDULED, CustomerFollowUp.Status.POSTPONED)
        ]
        active.sort(key=lambda followup: followup["scheduled_for"])
        next_followup = active[0] if active else None
        followup_state = (
            next_followup["bucket"]
            if next_followup
            else ("completed" if customer_followups else "none")
        )
        activity_dates = [
            value
            for value in (
                [entry["created_at"] for entry in item["inquiries"]]
                + [entry["created_at"] for entry in notes]
                + [
                    entry.get("completed_at") or entry["scheduled_for"]
                    for entry in customer_followups
                ]
            )
            if value
        ]
        last_activity_at = max(activity_dates, default=item["created_at"])
        activities = [
            {
                "kind": "درخواست خرید",
                "title": inquiry["title"],
                "detail": inquiry["status_label"],
                "created_at": inquiry["created_at"],
                "url": inquiry["url"],
            }
            for inquiry in item["inquiries"]
        ]
        activities.extend(
            {
                "kind": "یادداشت",
                "title": note["text"],
                "detail": "ثبت‌شده در پرونده مشتری",
                "created_at": note["created_at"],
                "url": "",
            }
            for note in notes
        )
        activities.extend(
            {
                "kind": "پیگیری",
                "title": followup["title"],
                "detail": followup["status_label"],
                "created_at": followup.get("completed_at") or followup["scheduled_for"],
                "url": "",
            }
            for followup in customer_followups
        )
        activities.sort(key=lambda activity: activity["created_at"], reverse=True)
        item.update(
            {
                "category_label": CATEGORY_LABELS.get(item["category"], "سایر"),
                "crm_status_label": CUSTOMER_STATUS_LABELS.get(
                    item["crm_status"], "در حال بررسی"
                ),
                "notes": notes,
                "followups": customer_followups,
                "next_followup": next_followup,
                "followup_state": followup_state,
                "followup_state_label": dict(FOLLOWUP_FILTERS).get(
                    followup_state, "بدون پیگیری"
                ),
                "last_activity_at": last_activity_at,
                "last_activity_label": self._activity_label(last_activity_at),
                "inquiry_count": len(item["inquiries"]),
                "activities": activities,
                "url": reverse("inquiries:lead_detail", kwargs={"lead_id": item["id"]}),
            }
        )
        return item

    def _loaded_followups(self) -> list[dict]:
        if self._followup_cache is None:
            rows = CustomerFollowUp.objects.filter(business=self.business).select_related(
                "customer"
            )
            self._followup_cache = sorted(
                [self._decorate_followup(self._followup_record(row)) for row in rows],
                key=lambda item: (
                    self._followup_rank(item["bucket"]), item["scheduled_for"]
                ),
            )
        return self._followup_cache

    @staticmethod
    def _followup_record(
        followup: CustomerFollowUp,
        *,
        customer_name: str | None = None,
        customer_phone: str | None = None,
    ) -> dict:
        return {
            "id": str(followup.pk),
            "customer_id": str(followup.customer_id),
            "customer_name": customer_name or followup.customer.name,
            "customer_phone": customer_phone or followup.customer.phone,
            "title": followup.title,
            "scheduled_for": followup.scheduled_for,
            "reminder_minutes": followup.reminder_minutes,
            "priority": followup.priority,
            "status": followup.status,
            "note": followup.note,
            "related_context": followup.related_context,
            "completed_at": followup.completed_at,
        }

    def _decorate_followup(self, record: dict, *, customer: dict | None = None) -> dict:
        item = dict(record)
        item.update(
            {
                "customer_name": customer["name"] if customer else record["customer_name"],
                "customer_phone": customer["phone"] if customer else record["customer_phone"],
                "customer_url": reverse(
                    "inquiries:lead_detail", kwargs={"lead_id": record["customer_id"]}
                ),
                "status_label": FOLLOWUP_STATUS_LABELS.get(
                    record["status"], record["status"]
                ),
                "priority_label": PRIORITY_LABELS.get(record["priority"], "عادی"),
                "bucket": self._followup_bucket(record),
                "due_label": self._due_label(record["scheduled_for"], record["status"]),
                "scheduled_value": timezone.localtime(record["scheduled_for"]).strftime(
                    "%Y-%m-%dT%H:%M"
                ),
            }
        )
        return item

    def _get_lead(self, customer_id) -> CustomerLead:
        customer = CustomerLead.objects.filter(
            business=self.business, pk=customer_id
        ).first()
        if customer is None:
            raise ValueError("مشتری یافت نشد.")
        return customer

    def _locked_followup(self, followup_id) -> CustomerFollowUp:
        followup = (
            CustomerFollowUp.objects.select_for_update()
            .filter(business=self.business, pk=followup_id)
            .first()
        )
        if followup is None:
            raise ValueError("پیگیری یافت نشد.")
        return followup

    @staticmethod
    def _valid_reminder_minutes(value: int) -> int:
        value = int(value)
        if value < 0 or value > 10080:
            raise ValueError("زمان یادآوری معتبر نیست.")
        return value

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return timezone.make_aware(value) if timezone.is_naive(value) else value

    @staticmethod
    def _followup_bucket(followup: dict) -> str:
        if followup["status"] in (
            CustomerFollowUp.Status.COMPLETED,
            CustomerFollowUp.Status.CANCELLED,
        ):
            return "completed"
        due_date = timezone.localtime(followup["scheduled_for"]).date()
        today = timezone.localdate()
        if due_date < today:
            return "overdue"
        if due_date == today:
            return "today"
        return "upcoming"

    @staticmethod
    def _followup_rank(state: str) -> int:
        return {"overdue": 0, "today": 1, "upcoming": 2, "none": 3, "completed": 4}.get(
            state, 5
        )

    @staticmethod
    def _due_label(value: datetime, status: str) -> str:
        local = timezone.localtime(value)
        if status == CustomerFollowUp.Status.COMPLETED:
            return f"انجام‌شده در {local:%Y/%m/%d، %H:%M}"
        if status == CustomerFollowUp.Status.CANCELLED:
            return f"لغوشده · {local:%Y/%m/%d، %H:%M}"
        today = timezone.localdate()
        if local.date() < today:
            return f"{(today - local.date()).days} روز عقب‌افتاده · {local:%Y/%m/%d، %H:%M}"
        if local.date() == today:
            return f"امروز، ساعت {local:%H:%M}"
        if local.date() == today + timedelta(days=1):
            return f"فردا، ساعت {local:%H:%M}"
        return f"{local:%Y/%m/%d، %H:%M}"

    @staticmethod
    def _activity_label(value: datetime) -> str:
        local = timezone.localtime(value)
        today = timezone.localdate()
        if local.date() == today:
            return f"امروز، {local:%H:%M}"
        if local.date() == today - timedelta(days=1):
            return f"دیروز، {local:%H:%M}"
        return f"{local:%Y/%m/%d}"

    @staticmethod
    def _customer_search_text(customer: dict) -> str:
        values = [
            customer["name"], customer["phone"], *customer.get("tags", []),
            *customer.get("requested_products", []),
        ]
        return normalize_persian_text(" ".join(values)).casefold()
