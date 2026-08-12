"""Read-only admin for retired Contacts.

The Contact concept was replaced in V2 by the colleague Business directory: a
colleague is a Business, so two people at the same company can no longer become
two different debtors.

The model survives because ``LedgerEntry.contact`` is a PROTECT FK holding
pre-V2 rows whose counterparty could not be mapped to a Business. Those rows are
history, so this admin is read-only — a half-edited legacy contact would change
what a historical entry says it was filed under.
"""

from __future__ import annotations

from django.contrib import admin

from .models import Contact


@admin.register(Contact)
class LegacyContactAdmin(admin.ModelAdmin):
    list_display = ("display_name", "business", "linked_business", "phone", "is_active")
    list_filter = ("is_active",)
    search_fields = ("display_name", "phone")
    raw_id_fields = ("business", "linked_business", "created_by")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
