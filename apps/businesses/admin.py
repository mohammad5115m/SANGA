from django.contrib import admin

from apps.core.admin import NoDeleteAdmin

from .models import Business, BusinessMembership

# Warehouse is deliberately not registered. The user-facing warehouse workflow
# was removed in V2 and the model survives only to keep the migration graph
# whole and its rows readable — location now lives on the item. Leaving it in
# admin invited operators to create records nothing reads. See
# docs/v2-migration-strategy.md §4.4.


class MembershipInline(admin.TabularInline):
    model = BusinessMembership
    extra = 0
    autocomplete_fields = ("user",)


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
        "city",
        "plan",
        "seat_limit",
        "active_until",
        "verification_status",
        "status",
    )
    list_filter = ("plan", "verification_status", "status")
    search_fields = ("name", "slug", "phone", "city")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [MembershipInline]
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


@admin.register(BusinessMembership)
class BusinessMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "business", "role", "status", "joined_at")
    list_filter = ("role", "status")
    search_fields = ("user__phone", "user__full_name", "business__name")
    autocomplete_fields = ("user", "business")
