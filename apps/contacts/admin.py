from __future__ import annotations

from django.contrib import admin

from .models import Contact


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ("display_name", "business", "phone", "is_active")
    list_filter = ("is_active",)
    search_fields = ("display_name", "phone")
    raw_id_fields = ("business", "linked_business", "created_by")
    readonly_fields = ("created_at", "updated_at")
