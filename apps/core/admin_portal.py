"""Owner-only platform operations and overview for the Persian admin console."""

from __future__ import annotations

from urllib.parse import urlencode

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.models import ADDITION, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import RegistrationRequest, User
from apps.businesses.entitlements import EntitlementError, require_seat_available
from apps.businesses.models import Business, BusinessMembership
from apps.businesses.permissions import defaults_for_role
from apps.businesses.services import BusinessServiceError, create_business_for_owner
from apps.catalog.models import CustomCatalog
from apps.core.calendar import format_jalali
from apps.core.persian import normalize_digits, normalize_phone
from apps.inquiries.models import CustomerFollowUp, CustomerLead, Inquiry
from apps.inventory.models import InventoryLot, Product
from apps.invoicing.models import SalesInvoice
from apps.trading.models import Trade


def _valid_mobile(value: str) -> str:
    phone = normalize_phone(value)
    if not (phone.startswith("09") and len(phone) == 11):
        raise forms.ValidationError("شماره موبایل معتبر نیست؛ مانند ۰۹۱۲۳۴۵۶۷۸۹.")
    return phone


class BusinessProvisionForm(forms.Form):
    name = forms.CharField(label="نام کسب‌وکار", max_length=200, min_length=2)
    owner_phone = forms.CharField(
        label="موبایل مالک", max_length=20,
        widget=forms.TextInput(attrs={"type": "tel", "inputmode": "tel", "dir": "ltr", "autocomplete": "tel"}),
        help_text="مانند ۰۹۱۲۳۴۵۶۷۸۹؛ ارقام فارسی و انگلیسی پذیرفته می‌شوند.",
    )
    owner_name = forms.CharField(label="نام مالک", max_length=150, required=False)
    city = forms.CharField(label="شهر", max_length=100, required=False)
    province = forms.CharField(label="استان", max_length=100, required=False)
    plan = forms.ChoiceField(label="نوع دسترسی", choices=Business.Plan.choices)
    seat_limit = forms.IntegerField(label="تعداد کاربران مجاز", min_value=1, max_value=10000, initial=1)
    verified = forms.BooleanField(label="هویت کسب‌وکار بررسی و تأیید شده است", required=False)

    def clean_owner_phone(self):
        phone = _valid_mobile(self.cleaned_data["owner_phone"])
        existing = User.objects.filter(phone=phone).first()
        if existing and (not existing.is_active or existing.is_superuser):
            raise forms.ValidationError("این شماره برای مالک کسب‌وکار قابل استفاده نیست.")
        return phone


class MemberProvisionForm(forms.Form):
    business = forms.ModelChoiceField(label="کسب‌وکار", queryset=Business.objects.none())
    phone = forms.CharField(
        label="موبایل کاربر", max_length=20,
        widget=forms.TextInput(attrs={"type": "tel", "inputmode": "tel", "dir": "ltr", "autocomplete": "tel"}),
        help_text="مانند ۰۹۱۲۳۴۵۶۷۸۹؛ ارقام فارسی و انگلیسی پذیرفته می‌شوند.",
    )
    full_name = forms.CharField(label="نام کاربر", max_length=150, required=False)
    role = forms.ChoiceField(
        label="نقش در کسب‌وکار",
        choices=[
            (BusinessMembership.Role.MANAGER, "مدیر"),
            (BusinessMembership.Role.STAFF, "کارمند"),
            (BusinessMembership.Role.VIEWER, "بازدیدکننده"),
        ],
        initial=BusinessMembership.Role.STAFF,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["business"].queryset = Business.objects.filter(status=Business.Status.ACTIVE).order_by("name")

    def clean_phone(self):
        phone = _valid_mobile(self.cleaned_data["phone"])
        existing = User.objects.filter(phone=phone).first()
        if existing and (not existing.is_active or existing.is_superuser):
            raise forms.ValidationError("این شماره برای کاربر کسب‌وکار قابل استفاده نیست.")
        return phone

    def clean(self):
        data = super().clean()
        if data.get("business") and data.get("phone"):
            if BusinessMembership.objects.filter(business=data["business"], user__phone=data["phone"]).exists():
                raise forms.ValidationError("این شماره قبلاً عضو این کسب‌وکار شده است؛ عضویت موجود را ویرایش کنید.")
        return data


def _log_addition(request: HttpRequest, obj, detail: str) -> None:
    LogEntry.objects.create(
        user_id=request.user.pk,
        content_type=ContentType.objects.get_for_model(obj),
        object_id=str(obj.pk),
        object_repr=str(obj)[:200],
        action_flag=ADDITION,
        change_message=detail,
    )


def _form_context(request: HttpRequest, *, form, title: str, lead: str, submit: str):
    return {
        **admin.site.each_context(request),
        "title": title,
        "form": form,
        "lead": lead,
        "submit_label": submit,
    }


def provision_business(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = BusinessProvisionForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            try:
                with transaction.atomic():
                    owner, _ = User.objects.get_or_create(
                        phone=data["owner_phone"],
                        defaults={"full_name": data["owner_name"].strip()},
                    )
                    if data["owner_name"].strip() and not owner.full_name:
                        owner.full_name = data["owner_name"].strip()
                        owner.save(update_fields=["full_name"])
                    business = create_business_for_owner(
                        owner=owner,
                        name=data["name"],
                        city=data["city"],
                        province=data["province"],
                        verified=data["verified"],
                    )
                    business.plan = data["plan"]
                    business.seat_limit = data["seat_limit"]
                    business.save(update_fields=["plan", "seat_limit", "updated_at"])
                    _log_addition(request, business, "ایجاد کسب‌وکار همراه با مالک از پنل مدیریت")
            except BusinessServiceError as exc:
                form.add_error(None, exc.message)
            else:
                messages.success(request, f"کسب‌وکار «{business.name}» و دسترسی مالک آن ساخته شد.")
                return redirect("admin:businesses_business_change", business.pk)
    else:
        form = BusinessProvisionForm(initial={"plan": Business.Plan.SELLER, "seat_limit": 1})
    return TemplateResponse(
        request,
        "admin/provision_form.html",
        _form_context(
            request,
            form=form,
            title="افزودن کسب‌وکار و مالک",
            lead="حساب مالک، عضویت و کسب‌وکار در یک مرحله ساخته می‌شوند. تأیید را تنها پس از بررسی هویت فعال کنید.",
            submit="ساخت کسب‌وکار",
        ),
    )


def provision_member(request: HttpRequest) -> HttpResponse:
    initial = {}
    if request.method == "POST":
        form = MemberProvisionForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            try:
                with transaction.atomic():
                    business = Business.objects.select_for_update().get(pk=data["business"].pk)
                    require_seat_available(business)
                    user, _ = User.objects.get_or_create(
                        phone=data["phone"],
                        defaults={"full_name": data["full_name"].strip()},
                    )
                    if data["full_name"].strip() and not user.full_name:
                        user.full_name = data["full_name"].strip()
                        user.save(update_fields=["full_name"])
                    if BusinessMembership.objects.filter(business=business, user=user).exists():
                        raise forms.ValidationError("این شماره قبلاً عضو این کسب‌وکار شده است.")
                    membership = BusinessMembership.objects.create(
                        business=business,
                        user=user,
                        role=data["role"],
                        permissions=defaults_for_role(data["role"]),
                        status=BusinessMembership.Status.ACTIVE,
                    )
                    _log_addition(request, membership, "افزودن کاربر از پنل مدیریت")
            except EntitlementError as exc:
                form.add_error("business", exc.message)
            except forms.ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, f"{user} به «{business.name}» اضافه شد.")
                return redirect("admin:businesses_businessmembership_change", membership.pk)
    else:
        business_id = request.GET.get("business")
        if business_id:
            try:
                if Business.objects.filter(pk=business_id).exists():
                    initial["business"] = business_id
            except (ValueError, TypeError, ValidationError):
                pass
        form = MemberProvisionForm(initial=initial)
    return TemplateResponse(
        request,
        "admin/provision_form.html",
        _form_context(
            request,
            form=form,
            title="افزودن کاربر به کسب‌وکار",
            lead="شمارهٔ جدید حساب کاربری می‌گیرد و به تیم کسب‌وکار وصل می‌شود. ظرفیت مجاز پیش از ثبت کنترل می‌شود.",
            submit="افزودن کاربر",
        ),
    )


_DASHBOARD_SECTIONS = (
    (
        "کسب‌وکار و دسترسی",
        "حساب‌ها، اعضا و وضعیت فعالیت",
        (
            ("کسب‌وکارها", "businesses", "Business"),
            ("عضویت‌ها", "businesses", "BusinessMembership"),
            ("کاربران", "accounts", "User"),
            ("درخواست‌های ثبت‌نام", "accounts", "RegistrationRequest"),
        ),
    ),
    (
        "محصول و ویترین",
        "موجودی، قیمت و کاتالوگ",
        (
            ("محصولات قابل فروش", "inventory", "InventoryLot"),
            ("محصولات", "inventory", "Product"),
            ("قیمت‌ها", "pricing", "LotPrice"),
            ("کاتالوگ‌ها", "catalog", "CustomCatalog"),
        ),
    ),
    (
        "مشتریان و درخواست‌ها",
        "استعلام‌ها و پیگیری‌های فروش",
        (
            ("استعلام‌ها", "inquiries", "Inquiry"),
            ("مشتریان", "inquiries", "CustomerLead"),
            ("پیگیری‌ها", "inquiries", "CustomerFollowUp"),
        ),
    ),
    (
        "فروش و مالی",
        "فاکتورها، معاملات و سوابق مالی",
        (
            ("فاکتورهای فروش", "invoicing", "SalesInvoice"),
            ("معاملات", "trading", "Trade"),
            ("دفتر حساب", "accounting", "LedgerEntry"),
        ),
    ),
)

_PRIMARY_MODELS = (
    ("کسب‌وکارها", "businesses", "Business"),
    ("کاربران", "accounts", "User"),
    ("محصولات", "inventory", "InventoryLot"),
    ("استعلام‌ها", "inquiries", "Inquiry"),
    ("فاکتورها", "invoicing", "SalesInvoice"),
    ("دفتر حساب", "accounting", "LedgerEntry"),
)


def navigation_context(available_apps: list[dict]) -> dict:
    model_lookup = {
        (app["app_label"], model["object_name"]): model for app in available_apps for model in app["models"]
    }
    quick_nav = [
        {"name": name, "url": model_lookup[(app, model)]["admin_url"]}
        for name, app, model in _PRIMARY_MODELS
        if (app, model) in model_lookup and model_lookup[(app, model)].get("admin_url")
    ]
    sections = []
    for title, description, entries in _DASHBOARD_SECTIONS:
        models = [
            {"name": name, "admin_url": model_lookup[(app, model)]["admin_url"]}
            for name, app, model in entries
            if (app, model) in model_lookup and model_lookup[(app, model)].get("admin_url")
        ]
        if models:
            sections.append({"title": title, "description": description, "models": models})
    return {"admin_quick_nav": quick_nav, "admin_sections": sections}


def _object_url(app: str, model: str, pk) -> str:
    return reverse(f"admin:{app}_{model}_change", args=[pk])


def _list_url(app: str, model: str, **filters) -> str:
    return reverse(f"admin:{app}_{model}_changelist") + "?" + urlencode(filters)


def _search_results(term: str) -> list[dict]:
    results = []
    sources = (
        (
            "کسب‌وکار",
            Business.objects.filter(Q(name__icontains=term) | Q(phone__icontains=term)),
            "businesses",
            "business",
            lambda obj: obj.name,
            lambda obj: obj.phone,
        ),
        (
            "کاربر",
            User.objects.filter(Q(phone__icontains=term) | Q(full_name__icontains=term)),
            "accounts",
            "user",
            lambda obj: obj.display_name,
            lambda obj: obj.phone,
        ),
        (
            "محصول",
            Product.objects.filter(
                Q(commercial_name__icontains=term) | Q(business__name__icontains=term)
            ).select_related("business"),
            "inventory",
            "product",
            lambda obj: obj.commercial_name,
            lambda obj: obj.business.name,
        ),
        (
            "فاکتور",
            SalesInvoice.objects.filter(Q(number__icontains=term) | Q(buyer_name__icontains=term)).select_related(
                "seller_business"
            ),
            "invoicing",
            "salesinvoice",
            lambda obj: obj.number or "فاکتور بدون شماره",
            lambda obj: obj.seller_business.name,
        ),
        (
            "استعلام",
            Inquiry.objects.filter(Q(name__icontains=term) | Q(phone__icontains=term)),
            "inquiries",
            "inquiry",
            lambda obj: obj.name or obj.phone,
            lambda obj: obj.phone,
        ),
        (
            "مشتری",
            CustomerLead.objects.filter(Q(name__icontains=term) | Q(phone__icontains=term)),
            "inquiries",
            "customerlead",
            lambda obj: obj.name or obj.phone,
            lambda obj: obj.phone,
        ),
    )
    for kind, queryset, app, model, label, detail in sources:
        for obj in queryset[:4]:
            results.append(
                {
                    "kind": kind,
                    "label": label(obj),
                    "detail": detail(obj),
                    "url": _object_url(app, model, obj.pk),
                }
            )
    return results


def dashboard_context(request: HttpRequest) -> dict:
    now = timezone.now()
    pending_businesses = Business.objects.filter(
        verification_status=Business.VerificationStatus.PENDING,
    ).order_by("created_at")
    new_inquiries = Inquiry.objects.filter(status=Inquiry.Status.NEW)
    pending_invoices = SalesInvoice.objects.filter(status=SalesInvoice.Status.AWAITING_CONFIRMATION)
    overdue_followups = CustomerFollowUp.objects.filter(
        status__in=(CustomerFollowUp.Status.SCHEDULED, CustomerFollowUp.Status.POSTPONED),
        scheduled_for__lt=now,
    )
    term = normalize_digits((request.GET.get("q") or "").strip()[:80])
    search_ready = len(term) >= 2
    return {
        "admin_today": format_jalali(timezone.localdate()),
        "admin_stats": [
            {
                "label": "کسب‌وکار فعال",
                "value": Business.objects.filter(status=Business.Status.ACTIVE).count(),
                "url": _list_url("businesses", "business", status__exact=Business.Status.ACTIVE),
            },
            {
                "label": "کاربر فعال",
                "value": User.objects.filter(is_active=True, is_superuser=False).count(),
                "url": _list_url("accounts", "user", is_active__exact=1, is_superuser__exact=0),
            },
            {
                "label": "محصول منتشرشده",
                "value": InventoryLot.objects.filter(
                    status=InventoryLot.Status.ACTIVE, is_visible=True, deleted_at__isnull=True
                ).count(),
                "url": _list_url(
                    "inventory", "inventorylot", status__exact=InventoryLot.Status.ACTIVE,
                    is_visible__exact=1, deleted_at__isnull=True,
                ),
            },
            {"label": "فروش نهایی", "value": Trade.objects.count(), "url": reverse("admin:trading_trade_changelist")},
        ],
        "admin_queue": [
            {
                "label": "درخواست ثبت‌نام در انتظار بررسی",
                "count": RegistrationRequest.objects.filter(status=RegistrationRequest.Status.PENDING).count(),
                "url": reverse("admin:accounts_registrationrequest_changelist") + "?status__exact=pending",
                "tone": "amber",
            },
            {
                "label": "کسب‌وکار در انتظار تأیید",
                "count": pending_businesses.count(),
                "url": reverse("admin:businesses_business_changelist") + "?verification_status__exact=pending",
                "tone": "amber",
            },
            {
                "label": "استعلام جدید",
                "count": new_inquiries.count(),
                "url": reverse("admin:inquiries_inquiry_changelist") + "?status__exact=new",
                "tone": "blue",
            },
            {
                "label": "فاکتور در انتظار تأیید",
                "count": pending_invoices.count(),
                "url": reverse("admin:invoicing_salesinvoice_changelist") + "?status__exact=awaiting_confirmation",
                "tone": "green",
            },
            {
                "label": "پیگیری عقب‌افتاده",
                "count": overdue_followups.count(),
                "url": _list_url(
                    "inquiries", "customerfollowup", status__in=",".join((
                        CustomerFollowUp.Status.SCHEDULED, CustomerFollowUp.Status.POSTPONED,
                    )),
                    scheduled_for__lt=now.isoformat(),
                ),
                "tone": "red",
            },
        ],
        "admin_pending_businesses": list(pending_businesses[:5]),
        "admin_recent_businesses": list(Business.objects.order_by("-created_at")[:5]),
        "admin_activity": list(LogEntry.objects.select_related("content_type").order_by("-action_time")[:8]),
        "admin_search_term": term,
        "admin_search_ready": search_ready,
        "admin_search_results": _search_results(term) if search_ready else [],
        "admin_catalog_count": CustomCatalog.objects.filter(is_active=True).count(),
    }
