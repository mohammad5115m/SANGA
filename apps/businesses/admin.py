from django import forms
from django.contrib import admin
from django.db import transaction
from django.urls import reverse

from apps.core.admin import NoDeleteAdmin
from apps.core.calendar import format_jalali

from .models import Business, BusinessMembership
from .services import BusinessServiceError, set_membership_status, validate_membership_status_change

# Warehouse is deliberately not registered. The user-facing warehouse workflow
# was removed in V2 and the model survives only to keep the migration graph
# whole and its rows readable — location now lives on the item. Leaving it in
# admin invited operators to create records nothing reads. See
# docs/v2-migration-strategy.md §4.4.


class MembershipInline(admin.TabularInline):
    model = BusinessMembership
    extra = 0
    autocomplete_fields = ("user",)
    readonly_fields = ("user", "business", "role", "status", "permissions", "joined_at")
    can_delete = False

    def has_add_permission(self, request, obj=None) -> bool:
        # The owner-only provisioning form enforces the tenant's seat limit.
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


class VerificationStatusFilter(admin.ChoicesFieldListFilter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.title = "وضعیت تأیید"


class BusinessStatusFilter(admin.ChoicesFieldListFilter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.title = "وضعیت فعالیت"


class MembershipRoleFilter(admin.ChoicesFieldListFilter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.title = "نقش"


class MembershipStatusFilter(admin.ChoicesFieldListFilter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.title = "وضعیت عضویت"


@admin.register(Business)
class BusinessAdmin(NoDeleteAdmin):
    """A tenant is suspended, never deleted from here.

    Almost everything a Business owns cascades from it: products, prices, media,
    catalogs, inquiries, leads, memberships. Some counterparty links are PROTECT
    and would refuse, but that is incidental — whether a delete succeeds depends
    on which relationships that particular tenant happens to have, which is not a
    safety property. A Business with no trades yet would simply vanish, taking
    everything with it and leaving nothing to restore from.

    «معلق» removes a tenant from the network and stops it writing while keeping
    its records intact and its debts settleable. That is what "deleting" a
    business means here. A genuine purge — for a legal erasure request — needs to
    be a deliberate, audited procedure, not a button next to the save bar.
    """
    list_display = (
        "name",
        "slug",
        "business_city",
        "plan",
        "seat_limit",
        "active_until",
        "business_verification_status",
        "business_status",
    )
    list_filter = (
        "plan",
        ("verification_status", VerificationStatusFilter),
        ("status", BusinessStatusFilter),
    )
    search_fields = ("name", "slug", "phone", "city")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [MembershipInline]
    change_list_template = "admin/provision_changelist.html"

    def has_add_permission(self, request) -> bool:
        # Creating a tenant also needs its owner membership.
        return False

    @admin.display(description="شهر", ordering="city")
    def business_city(self, obj):
        return obj.city

    @admin.display(description="وضعیت تأیید", ordering="verification_status")
    def business_verification_status(self, obj):
        return obj.get_verification_status_display()

    @admin.display(description="وضعیت فعالیت", ordering="status")
    def business_status(self, obj):
        return obj.get_status_display()

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        field = super().formfield_for_dbfield(db_field, request, **kwargs)
        labels = {
            "phone": "تلفن", "city": "شهر", "province": "استان",
            "address": "نشانی", "website": "وب‌سایت", "logo": "نشان",
            "status": "وضعیت فعالیت", "verification_status": "وضعیت تأیید",
            "settings": "تنظیمات", "onboarding_step": "مرحلهٔ راه‌اندازی",
            "onboarding_completed_at": "تاریخ تکمیل راه‌اندازی",
        }
        if field is not None and db_field.name in labels:
            field.label = labels[db_field.name]
        return field

    def changelist_view(self, request, extra_context=None):
        return super().changelist_view(request, {
            **(extra_context or {}),
            "title": "کسب‌وکارها",
            "provision_url": reverse("admin:provision_business"),
            "provision_label": "افزودن کسب‌وکار و مالک",
        })
    fieldsets = (
        (None, {"fields": ("name", "slug", "logo")}),
        ("تماس", {"fields": ("phone", "city", "province", "address", "website")}),
        (
            "اشتراک",
            {
                "fields": ("plan", "seat_limit", "active_until"),
                "description": (
                    "پلن «فقط مشاهده» اجازه ثبت و انتشار محصول، فروش و صدور فاکتور را نمی‌دهد. "
                    "خالی گذاشتن «اعتبار تا» یعنی بدون انقضا."
                ),
            },
        ),
        ("وضعیت", {"fields": ("status", "verification_status")}),
        ("پیشرفته", {"classes": ("collapse",), "fields": ("settings", "onboarding_step", "onboarding_completed_at")}),
    )


class MembershipStatusForm(forms.ModelForm):
    class Meta:
        model = BusinessMembership
        fields = ("status",)

    def clean_status(self):
        status = self.cleaned_data["status"]
        try:
            validate_membership_status_change(membership=self.instance, status=status)
        except BusinessServiceError as exc:
            raise forms.ValidationError(exc.message) from exc
        return status


@admin.register(BusinessMembership)
class BusinessMembershipAdmin(admin.ModelAdmin):
    form = MembershipStatusForm
    list_display = ("member_user", "member_business", "member_role", "member_status", "member_joined_at")
    list_filter = (("role", MembershipRoleFilter), ("status", MembershipStatusFilter))
    search_fields = ("user__phone", "user__full_name", "business__name")
    autocomplete_fields = ("user", "business")
    change_list_template = "admin/provision_changelist.html"

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        if request.method == "POST" and object_id:
            # Hold the same tenant lock as provisioning across validation AND
            # saving, so a lost seat becomes a bound form error, never a 500.
            with transaction.atomic():
                member = self.get_object(request, object_id)
                if member is not None:
                    Business.objects.select_for_update().get(pk=member.business_id)
                return super().changeform_view(request, object_id, form_url, extra_context)
        return super().changeform_view(request, object_id, form_url, extra_context)

    def save_model(self, request, obj, form, change):
        set_membership_status(membership=obj, status=form.cleaned_data["status"], actor=request.user)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("user", "business")

    @admin.display(description="کاربر", ordering="user")
    def member_user(self, obj):
        return obj.user

    @admin.display(description="کسب‌وکار", ordering="business")
    def member_business(self, obj):
        return obj.business

    @admin.display(description="نقش", ordering="role")
    def member_role(self, obj):
        return obj.get_role_display()

    @admin.display(description="وضعیت", ordering="status")
    def member_status(self, obj):
        return obj.get_status_display()

    @admin.display(description="تاریخ عضویت", ordering="joined_at")
    def member_joined_at(self, obj):
        return format_jalali(obj.joined_at, with_time=True)

    @admin.display(description="دسترسی‌ها")
    def member_permissions(self, obj):
        if obj.role == BusinessMembership.Role.OWNER:
            return "دسترسی کامل مالک"
        return f"{len(obj.permissions or [])} دسترسی ثبت‌شده"

    def get_fields(self, request, obj=None):
        fields = (
            "member_user", "member_business", "member_role",
            "member_joined_at", "member_permissions",
        )
        if obj is not None and obj.role != BusinessMembership.Role.OWNER:
            return (*fields, "status")
        return fields

    def get_readonly_fields(self, request, obj=None):
        return (
            "member_user", "member_business", "member_role",
            "member_joined_at", "member_permissions",
        )

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        field = super().formfield_for_dbfield(db_field, request, **kwargs)
        if field is not None and db_field.name == "status":
            field.label = "وضعیت عضویت"
        return field

    def has_change_permission(self, request, obj=None) -> bool:
        return (obj is None or obj.role != BusinessMembership.Role.OWNER) and super().has_change_permission(
            request, obj,
        )

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    def has_add_permission(self, request) -> bool:
        return False

    def changelist_view(self, request, extra_context=None):
        return super().changelist_view(request, {
            **(extra_context or {}),
            "title": "عضویت‌ها",
            "provision_url": reverse("admin:provision_member"),
            "provision_label": "افزودن کاربر به کسب‌وکار",
        })
