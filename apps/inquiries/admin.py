from django.contrib import admin

from .models import Inquiry


@admin.register(Inquiry)
class InquiryAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "business", "status", "source", "created_at")
    list_filter = ("status", "source")
    search_fields = ("name", "phone", "business__name", "message")
    autocomplete_fields = ("business", "lot", "custom_catalog", "requester", "assignee")
