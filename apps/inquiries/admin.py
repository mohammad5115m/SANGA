from django.contrib import admin

from .models import (
    CustomerFollowUp,
    CustomerLead,
    CustomerNote,
    FollowUpReminderRead,
    Inquiry,
    InquiryItem,
)


class InquiryItemInline(admin.TabularInline):
    model = InquiryItem
    extra = 0


@admin.register(CustomerLead)
class CustomerLeadAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "phone",
        "business",
        "category",
        "crm_status",
        "phone_verified_at",
        "updated_at",
    )
    list_filter = ("business", "category", "crm_status")
    search_fields = ("name", "phone")


@admin.register(CustomerNote)
class CustomerNoteAdmin(admin.ModelAdmin):
    list_display = ("customer", "business", "author", "created_at")
    list_filter = ("business",)
    search_fields = ("customer__name", "customer__phone", "text")
    autocomplete_fields = ("business", "customer", "author")


@admin.register(CustomerFollowUp)
class CustomerFollowUpAdmin(admin.ModelAdmin):
    list_display = ("title", "customer", "business", "status", "priority", "scheduled_for")
    list_filter = ("business", "status", "priority")
    search_fields = ("title", "customer__name", "customer__phone", "related_context")
    autocomplete_fields = ("business", "customer", "created_by")


@admin.register(FollowUpReminderRead)
class FollowUpReminderReadAdmin(admin.ModelAdmin):
    list_display = ("followup", "user", "read_at")
    search_fields = ("followup__title", "user__phone")
    autocomplete_fields = ("followup", "user")


@admin.register(Inquiry)
class InquiryAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "business", "status", "source", "created_at")
    list_filter = ("status", "source")
    search_fields = ("name", "phone", "business__name", "message")
    autocomplete_fields = ("business", "lot", "custom_catalog", "requester")
    inlines = [InquiryItemInline]
