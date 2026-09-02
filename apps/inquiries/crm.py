"""Lightweight CRM view models and development demo state.

The templates consume one stable customer/follow-up contract. Today that
contract combines real ``CustomerLead``/``Inquiry`` rows with optional,
session-backed CRM fields. A persistent implementation can replace this
repository without changing the views or templates.

Demo records are enabled only by ``SANGA_CRM_DEMO_ENABLED`` and are always
derived inside the current Business boundary. Production forces that setting
off; no fictional row is ever written to the database.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.db.models import Prefetch
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.core.persian import normalize_persian_text

from .models import CustomerLead, Inquiry

SESSION_KEY = "sanga_crm_session_v1"

CATEGORY_CHOICES = (
    ("builder", "سازنده"),
    ("consumer", "مصرف‌کننده نهایی"),
    ("warehouse", "انباردار"),
    ("contractor", "پیمانکار"),
    ("architect", "معمار / طراح"),
    ("partner", "فروشنده / همکار"),
    ("other", "سایر"),
)
CATEGORY_LABELS = dict(CATEGORY_CHOICES)

CUSTOMER_STATUS_CHOICES = (
    ("new", "مشتری جدید"),
    ("active", "در حال بررسی"),
    ("negotiating", "در حال مذاکره"),
    ("won", "خرید انجام‌شده"),
    ("inactive", "غیرفعال"),
)
CUSTOMER_STATUS_LABELS = dict(CUSTOMER_STATUS_CHOICES)

FOLLOWUP_STATUS_CHOICES = (
    ("scheduled", "زمان‌بندی‌شده"),
    ("completed", "انجام‌شده"),
    ("postponed", "به‌تعویق‌افتاده"),
    ("cancelled", "لغوشده"),
)
FOLLOWUP_STATUS_LABELS = dict(FOLLOWUP_STATUS_CHOICES)

PRIORITY_CHOICES = (
    ("normal", "عادی"),
    ("high", "مهم"),
    ("urgent", "فوری"),
)
PRIORITY_LABELS = dict(PRIORITY_CHOICES)

FOLLOWUP_FILTERS = (
    ("", "همه"),
    ("overdue", "عقب‌افتاده"),
    ("today", "امروز"),
    ("upcoming", "آینده"),
    ("completed", "انجام‌شده"),
)

_DEMO_CUSTOMERS = (
    {
        "key": "sara-ahmadi",
        "name": "سارا احمدی",
        "phone": "09121458732",
        "category": "architect",
        "tags": ["پروژه ویلایی", "نمای روشن"],
        "crm_status": "negotiating",
        "requested_products": ["سنگ تراورتن عباس‌آباد روشن", "سنگ مرمریت دهبید"],
        "current_needs": "نمای ویلای لواسان، حدود ۳۲۰ متر مربع؛ رنگ روشن و رگه کم",
        "note": "نمونه حضوری پسندیده شده؛ تصمیم نهایی با کارفرماست.",
        "inquiries": (
            ("سنگ تراورتن عباس‌آباد روشن، سنگ مرمریت دهبید", "contacted", -8),
            ("سنگ تراورتن کرم", "closed", -42),
        ),
    },
    {
        "key": "mehdi-kazemi",
        "name": "مهدی کاظمی",
        "phone": "09122649108",
        "category": "contractor",
        "tags": ["خرید پروژه‌ای", "تحویل سریع"],
        "crm_status": "active",
        "requested_products": ["سنگ گرانیت نطنز فلیم"],
        "current_needs": "کف محوطه مجتمع اداری، ۱۸۰ متر مربع با تحویل این هفته",
        "note": "قبل از اعلام قیمت، هزینه باربری تا کرج بررسی شود.",
        "inquiries": (("سنگ گرانیت نطنز فلیم", "new", -2),),
    },
    {
        "key": "narges-rezaei",
        "name": "نرگس رضایی",
        "phone": "09351288410",
        "category": "consumer",
        "tags": ["بازسازی منزل"],
        "crm_status": "new",
        "requested_products": ["سنگ مرمر سفید"],
        "current_needs": "کانتر و دیوار بین کابینت؛ برای انتخاب ضخامت نیاز به مشاوره دارد.",
        "note": "تماس در ساعات بعدازظهر مناسب‌تر است.",
        "inquiries": (("سنگ مرمر سفید", "new", -1),),
    },
    {
        "key": "kamran-heydari",
        "name": "کامران حیدری",
        "phone": "09193107264",
        "category": "warehouse",
        "tags": ["خریدار تکراری", "اصفهان"],
        "crm_status": "won",
        "requested_products": ["سنگ چینی نی‌ریز"],
        "current_needs": "در حال حاضر نیاز بازی ندارد؛ برای موجودی ماه آینده پیگیری شود.",
        "note": "خرید قبلی ۲۲۰ متر مربع با موفقیت تحویل شد.",
        "inquiries": (("سنگ چینی نی‌ریز", "converted", -25),),
    },
)

_DEMO_FOLLOWUPS = (
    {
        "key": "sara-price",
        "customer_key": "sara-ahmadi",
        "title": "اعلام قیمت نهایی نمای ویلا",
        "day_offset": -1,
        "hour": 11,
        "minute": 30,
        "reminder_minutes": 60,
        "priority": "urgent",
        "status": "scheduled",
        "note": "قیمت تراورتن روشن و زمان آماده‌سازی را یک‌جا اعلام کنید.",
        "related_context": "درخواست نمای ویلا · تراورتن عباس‌آباد",
    },
    {
        "key": "mehdi-shipping",
        "customer_key": "mehdi-kazemi",
        "title": "هماهنگی هزینه باربری",
        "day_offset": 0,
        "hour": 14,
        "minute": 0,
        "reminder_minutes": 60,
        "priority": "high",
        "status": "scheduled",
        "note": "هزینه باربری تا کرج و موجودی قابل تحویل را تأیید کنید.",
        "related_context": "درخواست گرانیت نطنز فلیم",
    },
    {
        "key": "narges-consult",
        "customer_key": "narges-rezaei",
        "title": "مشاوره انتخاب ضخامت",
        "day_offset": 2,
        "hour": 16,
        "minute": 30,
        "reminder_minutes": 1440,
        "priority": "normal",
        "status": "scheduled",
        "note": "تفاوت ضخامت مناسب کانتر و دیوار را ساده توضیح دهید.",
        "related_context": "درخواست سنگ مرمر سفید",
    },
    {
        "key": "kamran-delivery",
        "customer_key": "kamran-heydari",
        "title": "اطمینان از تحویل سفارش قبلی",
        "day_offset": -4,
        "hour": 10,
        "minute": 0,
        "reminder_minutes": 60,
        "priority": "normal",
        "status": "completed",
        "note": "تحویل کامل تأیید شد و مشتری از کیفیت راضی بود.",
        "related_context": "خرید سنگ چینی نی‌ریز",
    },
)


def crm_demo_enabled() -> bool:
    return bool(getattr(settings, "SANGA_CRM_DEMO_ENABLED", False))


def crm_mode_notice() -> str:
    if not crm_demo_enabled():
        return ""
    return (
        "نمایش CRM در حالت دمو است. دسته‌بندی، یادداشت و پیگیری‌های جدید فقط در "
        "همین مرورگر نگه‌داری می‌شوند و هنوز ذخیره دائمی ندارند."
    )


class CRMRepository:
    """Tenant-safe presentation repository for the current CRM phase."""

    def __init__(self, request) -> None:
        self.request = request
        self.business = request.business
        self.business_key = str(request.business.pk)

    # -- public query contract -------------------------------------------------

    def list_customers(
        self,
        *,
        q: str = "",
        category: str = "",
        followup_state: str = "",
        status: str = "",
    ) -> list[dict]:
        base_customers = self._base_customers()
        followups = self._all_followups(customers=base_customers)
        customers = [
            self._decorate_customer(item, followups=followups) for item in base_customers
        ]
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
        target = str(customer_id)
        base_customers = self._base_customers()
        customer = next((item for item in base_customers if item["id"] == target), None)
        return (
            self._decorate_customer(
                customer, followups=self._all_followups(customers=base_customers)
            )
            if customer
            else None
        )

    def list_followups(self, *, state: str = "") -> list[dict]:
        followups = self._all_followups()
        if state:
            followups = [item for item in followups if item["bucket"] == state]
        return sorted(
            followups,
            key=lambda item: (
                self._followup_rank(item["bucket"]),
                item["scheduled_for"],
            ),
        )

    def followup_groups(self) -> list[dict]:
        followups = self._all_followups()
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
        read_ids = set(self._business_state().get("read_reminders", []))
        now = timezone.now()
        notifications = []
        for followup in self._all_followups():
            if followup["status"] not in {"scheduled", "postponed"}:
                continue
            reminder_at = followup["scheduled_for"] - timedelta(
                minutes=followup["reminder_minutes"]
            )
            if reminder_at > now:
                continue
            notifications.append(
                {
                    "id": f"crm-{followup['id']}",
                    "reminder_id": followup["id"],
                    "title": f"پیگیری {followup['customer_name']}",
                    "body": f"{followup['title']} · {followup['due_label']}",
                    "link": followup["customer_url"],
                    "is_read": followup["id"] in read_ids,
                    "created_at": reminder_at,
                    "kind_label": "یادآوری پیگیری",
                }
            )
        return sorted(notifications, key=lambda item: item["created_at"], reverse=True)

    def unread_reminder_count(self) -> int:
        read_ids = set(self._business_state().get("read_reminders", []))
        now = timezone.now()
        return sum(
            record["id"] not in read_ids
            and record["status"] in {"scheduled", "postponed"}
            and record["scheduled_for"]
            - timedelta(minutes=record["reminder_minutes"])
            <= now
            for record in self._followup_records().values()
        )

    # -- session-backed commands ---------------------------------------------

    def add_note(self, customer_id, text: str) -> None:
        customer = self.get_customer(customer_id)
        if customer is None:
            raise ValueError("مشتری یافت نشد.")
        value = (text or "").strip()
        if not value:
            raise ValueError("متن یادداشت را وارد کنید.")
        state = self._mutable_business_state()
        customer_state = state["customers"].setdefault(str(customer_id), {})
        notes = customer_state.setdefault("notes", [])
        notes.insert(0, {"text": value[:1000], "created_at": timezone.now().isoformat()})
        self._save_business_state(state)

    def update_customer(
        self,
        customer_id,
        *,
        category: str,
        crm_status: str,
        tags: list[str],
        current_needs: str,
    ) -> None:
        if self.get_customer(customer_id) is None:
            raise ValueError("مشتری یافت نشد.")
        state = self._mutable_business_state()
        customer_state = state["customers"].setdefault(str(customer_id), {})
        customer_state.update(
            {
                "category": category if category in CATEGORY_LABELS else "other",
                "crm_status": (
                    crm_status if crm_status in CUSTOMER_STATUS_LABELS else "active"
                ),
                "tags": [value.strip()[:60] for value in tags if value.strip()][:12],
                "current_needs": (current_needs or "").strip()[:1000],
            }
        )
        self._save_business_state(state)

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
        customer = self.get_customer(customer_id)
        if customer is None:
            raise ValueError("مشتری یافت نشد.")
        followup_id = str(uuid.uuid4())
        record = {
            "id": followup_id,
            "customer_id": str(customer_id),
            "title": (title or "").strip()[:160],
            "scheduled_for": scheduled_for.isoformat(),
            "reminder_minutes": int(reminder_minutes),
            "priority": priority if priority in PRIORITY_LABELS else "normal",
            "status": "scheduled",
            "note": (note or "").strip()[:1000],
            "related_context": (related_context or "").strip()[:255],
            "completed_at": "",
        }
        state = self._mutable_business_state()
        state["followups"][followup_id] = record
        self._save_business_state(state)
        return followup_id

    def complete_followup(self, followup_id, *, note: str = "") -> None:
        followup = self._find_followup(followup_id)
        if followup is None:
            raise ValueError("پیگیری یافت نشد.")
        followup["status"] = "completed"
        followup["completed_at"] = timezone.now()
        if note.strip():
            followup["note"] = note.strip()[:1000]
        self._store_followup(followup)

    def complete_customer_followup(self, customer_id, *, note: str = "") -> None:
        active = [
            item
            for item in self._all_followups()
            if item["customer_id"] == str(customer_id)
            and item["status"] in {"scheduled", "postponed"}
        ]
        if active:
            self.complete_followup(active[0]["id"], note=note)
            return
        self.schedule_followup(
            customer_id,
            title="پیگیری تلفنی",
            scheduled_for=timezone.now(),
            reminder_minutes=0,
            priority="normal",
            note=note,
        )
        created = next(
            item
            for item in self._all_followups()
            if item["customer_id"] == str(customer_id) and item["status"] == "scheduled"
        )
        self.complete_followup(created["id"], note=note)

    def postpone_followup(self, followup_id) -> None:
        followup = self._find_followup(followup_id)
        if followup is None:
            raise ValueError("پیگیری یافت نشد.")
        followup["scheduled_for"] = max(followup["scheduled_for"], timezone.now()) + timedelta(
            days=1
        )
        followup["status"] = "postponed"
        self._store_followup(followup)

    def reschedule_followup(self, followup_id, scheduled_for: datetime) -> None:
        followup = self._find_followup(followup_id)
        if followup is None:
            raise ValueError("پیگیری یافت نشد.")
        followup["scheduled_for"] = scheduled_for
        followup["status"] = "scheduled"
        self._store_followup(followup)

    def cancel_followup(self, followup_id) -> None:
        followup = self._find_followup(followup_id)
        if followup is None:
            raise ValueError("پیگیری یافت نشد.")
        followup["status"] = "cancelled"
        self._store_followup(followup)

    def mark_reminders_read(self, reminder_ids: list[str]) -> None:
        if not reminder_ids:
            return
        state = self._mutable_business_state()
        state["read_reminders"] = sorted(
            set(state.get("read_reminders", [])) | set(reminder_ids)
        )
        self._save_business_state(state)

    # -- customer view models -------------------------------------------------

    def _base_customers(self) -> list[dict]:
        customers = self._real_customers()
        if crm_demo_enabled():
            customers.extend(self._demo_customers())
        return customers

    def _real_customers(self) -> list[dict]:
        inquiry_qs = (
            Inquiry.objects.select_related("business")
            .prefetch_related("items")
            .order_by("-created_at")
        )
        leads = CustomerLead.objects.filter(business=self.business).prefetch_related(
            Prefetch("inquiries", queryset=inquiry_qs, to_attr="crm_inquiries")
        )
        result = []
        for lead in leads:
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
            open_exists = any(item["status"] in Inquiry.OPEN_STATUSES for item in inquiries)
            result.append(
                {
                    "id": str(lead.pk),
                    "name": lead.name,
                    "phone": lead.phone,
                    "is_verified": lead.is_verified,
                    "category": "other",
                    "tags": [],
                    "crm_status": "active" if open_exists else "new",
                    "requested_products": list(dict.fromkeys(requested_products)),
                    "current_needs": next(
                        (item["message"] for item in inquiries if item["message"]), ""
                    ),
                    "base_note": lead.note,
                    "base_notes": [],
                    "inquiries": inquiries,
                    "created_at": lead.created_at,
                }
            )
        return result

    def _demo_customers(self) -> list[dict]:
        now = timezone.now()
        result = []
        for blueprint in _DEMO_CUSTOMERS:
            customer_id = self._demo_uuid("customer", blueprint["key"])
            inquiries = []
            for index, (title, status, day_offset) in enumerate(blueprint["inquiries"]):
                inquiries.append(
                    {
                        "id": self._demo_uuid("inquiry", f"{blueprint['key']}-{index}"),
                        "title": title,
                        "status": status,
                        "status_label": dict(Inquiry.Status.choices).get(status, status),
                        "created_at": now + timedelta(days=day_offset),
                        "url": "",
                        "message": "",
                    }
                )
            result.append(
                {
                    "id": customer_id,
                    "name": blueprint["name"],
                    "phone": blueprint["phone"],
                    "is_verified": True,
                    "category": blueprint["category"],
                    "tags": list(blueprint["tags"]),
                    "crm_status": blueprint["crm_status"],
                    "requested_products": list(blueprint["requested_products"]),
                    "current_needs": blueprint["current_needs"],
                    "base_note": "",
                    "base_notes": [
                        {
                            "text": blueprint["note"],
                            "created_at": now - timedelta(days=3),
                        }
                    ],
                    "inquiries": inquiries,
                    "created_at": now - timedelta(days=55),
                }
            )
        return result

    def _decorate_customer(self, customer: dict, *, followups: list[dict] | None = None) -> dict:
        item = dict(customer)
        state = self._business_state().get("customers", {}).get(item["id"], {})
        for key in ("category", "crm_status", "current_needs"):
            if key in state:
                item[key] = state[key]
        if "tags" in state:
            item["tags"] = list(state["tags"])

        notes = list(item.get("base_notes", []))
        if item.get("base_note"):
            notes.append({"text": item["base_note"], "created_at": item["created_at"]})
        notes.extend(
            {
                "text": note["text"],
                "created_at": self._to_datetime(note.get("created_at")) or timezone.now(),
            }
            for note in state.get("notes", [])
        )
        notes.sort(key=lambda note: note["created_at"], reverse=True)

        customer_followups = [
            followup
            for followup in (followups if followups is not None else self._all_followups())
            if followup["customer_id"] == item["id"]
        ]
        active = [
            followup
            for followup in customer_followups
            if followup["status"] in {"scheduled", "postponed"}
        ]
        active.sort(key=lambda followup: followup["scheduled_for"])
        next_followup = active[0] if active else None
        followup_state = next_followup["bucket"] if next_followup else (
            "completed" if customer_followups else "none"
        )

        inquiry_dates = [entry["created_at"] for entry in item["inquiries"]]
        note_dates = [entry["created_at"] for entry in notes]
        followup_dates = [
            entry.get("completed_at") or entry["scheduled_for"]
            for entry in customer_followups
        ]
        activity_dates = [value for value in inquiry_dates + note_dates + followup_dates if value]
        last_activity_at = max(activity_dates, default=item["created_at"])

        activities = []
        for inquiry in item["inquiries"]:
            activities.append(
                {
                    "kind": "درخواست خرید",
                    "title": inquiry["title"],
                    "detail": inquiry["status_label"],
                    "created_at": inquiry["created_at"],
                    "url": inquiry["url"],
                }
            )
        for note in notes:
            activities.append(
                {
                    "kind": "یادداشت",
                    "title": note["text"],
                    "detail": "ثبت‌شده در پرونده مشتری",
                    "created_at": note["created_at"],
                    "url": "",
                }
            )
        for followup in customer_followups:
            activities.append(
                {
                    "kind": "پیگیری",
                    "title": followup["title"],
                    "detail": followup["status_label"],
                    "created_at": followup.get("completed_at") or followup["scheduled_for"],
                    "url": "",
                }
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

    # -- follow-up view models ------------------------------------------------

    def _all_followups(self, *, customers: list[dict] | None = None) -> list[dict]:
        customer_map = {
            item["id"]: item for item in (customers if customers is not None else self._base_customers())
        }
        records = self._followup_records()

        result = []
        for record in records.values():
            customer = customer_map.get(record["customer_id"])
            if customer is None:
                continue
            item = dict(record)
            item.update(
                {
                    "customer_name": customer["name"],
                    "customer_phone": customer["phone"],
                    "customer_url": reverse(
                        "inquiries:lead_detail", kwargs={"lead_id": customer["id"]}
                    ),
                    "status_label": FOLLOWUP_STATUS_LABELS.get(record["status"], record["status"]),
                    "priority_label": PRIORITY_LABELS.get(record["priority"], "عادی"),
                    "bucket": self._followup_bucket(record),
                    "due_label": self._due_label(record["scheduled_for"], record["status"]),
                    "scheduled_value": timezone.localtime(record["scheduled_for"]).strftime(
                        "%Y-%m-%dT%H:%M"
                    ),
                }
            )
            result.append(item)
        return result

    def _followup_records(self) -> dict[str, dict]:
        """Undecorated records, safe for the global notification badge path.

        Resolving customer names requires real lead queries; counting reminders
        does not. Keeping those paths separate avoids turning every app page into
        a CRM query merely because the shell includes an unread badge.
        """
        records: dict[str, dict] = {}
        if crm_demo_enabled():
            for blueprint in _DEMO_FOLLOWUPS:
                followup_id = self._demo_uuid("followup", blueprint["key"])
                customer_id = self._demo_uuid("customer", blueprint["customer_key"])
                scheduled_for = self._local_datetime(
                    timezone.localdate() + timedelta(days=blueprint["day_offset"]),
                    blueprint["hour"],
                    blueprint["minute"],
                )
                records[followup_id] = {
                    "id": followup_id,
                    "customer_id": customer_id,
                    "title": blueprint["title"],
                    "scheduled_for": scheduled_for,
                    "reminder_minutes": blueprint["reminder_minutes"],
                    "priority": blueprint["priority"],
                    "status": blueprint["status"],
                    "note": blueprint["note"],
                    "related_context": blueprint["related_context"],
                    "completed_at": scheduled_for if blueprint["status"] == "completed" else None,
                }

        for followup_id, raw in self._business_state().get("followups", {}).items():
            record = self._deserialize_followup(raw)
            if record:
                records[followup_id] = record
        return records

    def _find_followup(self, followup_id) -> dict | None:
        target = str(followup_id)
        return next((item for item in self._all_followups() if item["id"] == target), None)

    def _store_followup(self, followup: dict) -> None:
        state = self._mutable_business_state()
        state["followups"][followup["id"]] = self._serialize_followup(followup)
        self._save_business_state(state)

    @staticmethod
    def _serialize_followup(followup: dict) -> dict:
        return {
            "id": followup["id"],
            "customer_id": followup["customer_id"],
            "title": followup["title"],
            "scheduled_for": followup["scheduled_for"].isoformat(),
            "reminder_minutes": int(followup["reminder_minutes"]),
            "priority": followup["priority"],
            "status": followup["status"],
            "note": followup.get("note", ""),
            "related_context": followup.get("related_context", ""),
            "completed_at": (
                followup["completed_at"].isoformat() if followup.get("completed_at") else ""
            ),
        }

    def _deserialize_followup(self, raw: dict) -> dict | None:
        scheduled_for = self._to_datetime(raw.get("scheduled_for"))
        if scheduled_for is None:
            return None
        return {
            "id": str(raw.get("id")),
            "customer_id": str(raw.get("customer_id")),
            "title": str(raw.get("title") or "پیگیری مشتری"),
            "scheduled_for": scheduled_for,
            "reminder_minutes": int(raw.get("reminder_minutes") or 0),
            "priority": str(raw.get("priority") or "normal"),
            "status": str(raw.get("status") or "scheduled"),
            "note": str(raw.get("note") or ""),
            "related_context": str(raw.get("related_context") or ""),
            "completed_at": self._to_datetime(raw.get("completed_at")),
        }

    # -- scoped session and formatting helpers --------------------------------

    def _business_state(self) -> dict:
        root = self.request.session.get(SESSION_KEY, {})
        return root.get(self.business_key, {})

    def _mutable_business_state(self) -> dict:
        current = self._business_state()
        return {
            "customers": dict(current.get("customers", {})),
            "followups": dict(current.get("followups", {})),
            "read_reminders": list(current.get("read_reminders", [])),
        }

    def _save_business_state(self, state: dict) -> None:
        root = dict(self.request.session.get(SESSION_KEY, {}))
        root[self.business_key] = state
        self.request.session[SESSION_KEY] = root
        self.request.session.modified = True

    def _demo_uuid(self, kind: str, key: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"sanga:{self.business_key}:{kind}:{key}"))

    @staticmethod
    def _to_datetime(value) -> datetime | None:
        if isinstance(value, datetime):
            parsed = value
        elif value:
            parsed = parse_datetime(str(value))
        else:
            return None
        if parsed is None:
            return None
        if timezone.is_naive(parsed):
            return timezone.make_aware(parsed)
        return parsed

    @staticmethod
    def _local_datetime(day: date, hour: int, minute: int) -> datetime:
        return timezone.make_aware(datetime.combine(day, time(hour, minute)))

    @staticmethod
    def _followup_bucket(followup: dict) -> str:
        if followup["status"] in {"completed", "cancelled"}:
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
        if status == "completed":
            return f"انجام‌شده در {local:%Y/%m/%d، %H:%M}"
        today = timezone.localdate()
        if local.date() < today:
            days = (today - local.date()).days
            return f"{days} روز عقب‌افتاده · {local:%Y/%m/%d، %H:%M}"
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
            customer["name"],
            customer["phone"],
            *customer.get("tags", []),
            *customer.get("requested_products", []),
        ]
        return normalize_persian_text(" ".join(values)).casefold()
