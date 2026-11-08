from datetime import timedelta

from django import forms
from django.contrib import admin
from django.utils import timezone

from apps.core.admin import FixedBusinessAdmin, SameBusinessForm

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


class CustomerScopedForm(SameBusinessForm):
    business_relations = (("business", "pk"), ("customer", "business_id"))
    business_error = "مشتری باید متعلق به همین کسب‌وکار باشد."

    class Meta:
        fields = ("business", "customer", "author", "text")

class FollowUpAdminForm(CustomerScopedForm):
    reminder_minutes = forms.IntegerField(
        label="دقایق یادآوری", min_value=0, max_value=10080, initial=60,
        help_text="بین صفر تا ۱۰۰۸۰ دقیقه؛ زمان یادآوری خودکار محاسبه می‌شود.",
    )

    class Meta(CustomerScopedForm.Meta):
        fields = (
            "business", "customer", "created_by", "title", "scheduled_for", "reminder_minutes",
            "priority", "status", "note", "related_context",
        )


@admin.register(CustomerLead)
class CustomerLeadAdmin(FixedBusinessAdmin):
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
class CustomerNoteAdmin(FixedBusinessAdmin):
    form = CustomerScopedForm
    list_display = ("customer", "business", "author", "created_at")
    list_filter = ("business",)
    search_fields = ("customer__name", "customer__phone", "text")
    autocomplete_fields = ("business", "customer", "author")


@admin.register(CustomerFollowUp)
class CustomerFollowUpAdmin(FixedBusinessAdmin):
    form = FollowUpAdminForm
    list_display = ("title", "customer", "business", "status", "priority", "scheduled_for")
    list_filter = ("business", "status", "priority")
    search_fields = ("title", "customer__name", "customer__phone", "related_context")
    autocomplete_fields = ("business", "customer", "created_by")
    readonly_fields = ("remind_at", "completed_at", "created_at", "updated_at")

    def save_model(self, request, obj, form, change):
        obj.remind_at = obj.scheduled_for - timedelta(minutes=obj.reminder_minutes)
        if obj.status == CustomerFollowUp.Status.COMPLETED:
            obj.completed_at = obj.completed_at or timezone.now()
        elif obj.status in (CustomerFollowUp.Status.SCHEDULED, CustomerFollowUp.Status.POSTPONED):
            obj.completed_at = None
        super().save_model(request, obj, form, change)
        if change and {"scheduled_for", "reminder_minutes"}.intersection(form.changed_data):
            obj.reminder_reads.all().delete()


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
