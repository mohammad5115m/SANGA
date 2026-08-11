from __future__ import annotations

from django.contrib import admin

from .models import LedgerEntry


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = (
        "occurred_on",
        "business",
        "contact",
        "entry_type",
        "amount",
        "balance_delta",
        "balance_after",
    )
    list_filter = ("entry_type", "occurred_on")
    search_fields = ("description", "reference", "contact__display_name")
    raw_id_fields = ("business", "contact", "related_lot", "related_reservation", "reverses", "created_by")
    # Ledger entries are immutable; expose as read-only in admin too.
    readonly_fields = [f.name for f in LedgerEntry._meta.fields]

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
