from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import UserChangeForm

from .models import OTPChallenge, RegistrationRequest, User
from .password_auth import review_registration
from .services import OTPError


class AccountChangeForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = "__all__"


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    form = AccountChangeForm
    change_list_template = "admin/user_changelist.html"
    ordering = ("-date_joined",)
    list_display = ("phone", "full_name", "is_staff", "is_active", "date_joined")
    list_filter = ("is_active", "is_staff")
    search_fields = ("phone", "full_name", "email")
    fieldsets = (
        ("مشخصات", {"fields": ("phone", "full_name", "email", "is_active")}),
        ("رمز عبور", {"fields": ("password",)}),
        ("دسترسی پلتفرم", {"fields": ("is_staff", "is_superuser")}),
        ("تاریخ‌ها", {"fields": ("last_login", "date_joined")}),
    )
    readonly_fields = ("is_staff", "is_superuser", "last_login")

    def has_add_permission(self, request):
        # Users enter through the owner/member provisioning forms so nobody is
        # left with an account that has no business membership.
        return False


@admin.register(OTPChallenge)
class OTPChallengeAdmin(admin.ModelAdmin):
    list_display = ("phone", "purpose", "attempts", "is_used", "created_at", "expires_at")
    list_filter = ("purpose", "is_used")
    search_fields = ("phone",)
    readonly_fields = ("code_hash", "created_at", "expires_at", "request_ip", "user_agent")


@admin.register(RegistrationRequest)
class RegistrationRequestAdmin(admin.ModelAdmin):
    list_display = ("business_name", "full_name", "phone", "city", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("business_name", "full_name", "phone", "city")
    fields = ("business_name", "full_name", "phone", "city", "status", "created_at",
              "reviewed_at", "reviewed_by", "business")
    readonly_fields = fields
    actions = ("approve_requests", "reject_requests")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        # Review actions are available on the list; submitted details cannot
        # be rewritten on an individual request, including crafted POSTs.
        return obj is None and super().has_change_permission(request, obj)

    def _review(self, request, queryset, approve):
        for registration in queryset:
            try:
                review_registration(registration=registration, actor=request.user, approve=approve)
            except OTPError as exc:
                self.message_user(request, exc.message, level="error")
            else:
                self.log_change(request, registration, "تأیید ثبت‌نام" if approve else "رد ثبت‌نام")
        self.message_user(request, "بررسی درخواست‌های انتخاب‌شده انجام شد.")

    @admin.action(description="تأیید ثبت‌نام و ساخت حساب مالک و کسب‌وکار")
    def approve_requests(self, request, queryset):
        self._review(request, queryset, True)

    @admin.action(description="رد درخواست ثبت‌نام")
    def reject_requests(self, request, queryset):
        self._review(request, queryset, False)
