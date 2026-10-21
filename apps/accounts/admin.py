from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import OTPChallenge, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    change_list_template = "admin/user_changelist.html"
    ordering = ("-date_joined",)
    list_display = ("phone", "full_name", "is_staff", "is_active", "date_joined")
    list_filter = ("is_active", "is_staff")
    search_fields = ("phone", "full_name", "email")
    fieldsets = (
        ("مشخصات", {"fields": ("phone", "full_name", "email", "is_active")}),
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
