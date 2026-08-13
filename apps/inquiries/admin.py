from django.contrib import admin

from .models import CustomerLead, Inquiry, InquiryItem


class InquiryItemInline(admin.TabularInline):
    model = InquiryItem
    extra = 0


@admin.register(CustomerLead)
class CustomerLeadAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "business", "phone_verified_at", "updated_at")
    list_filter = ("business",)
    search_fields = ("name", "phone")


@admin.register(Inquiry)
class InquiryAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "business", "status", "source", "created_at")
    list_filter = ("status", "source")
    search_fields = ("name", "phone", "business__name", "message")
    autocomplete_fields = ("business", "lot", "custom_catalog", "requester")
    inlines = [InquiryItemInline]
