from __future__ import annotations

from django.contrib import admin

from apps.core.admin_export import csv_response

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
    raw_id_fields = ("business", "contact", "related_lot", "related_offer", "reverses", "created_by")
    # Ledger entries are immutable; expose as read-only in admin too.
    readonly_fields = [f.name for f in LedgerEntry._meta.fields]
    actions = ["export_selected_csv"]

    @admin.action(description="دریافت CSV رویدادهای انتخاب‌شده", permissions=["view"])
    def export_selected_csv(self, request, queryset):
        rows = (
            (
                entry.occurred_on, entry.business.name,
                entry.counterparty_business.name if entry.counterparty_business else (
                    entry.local_counterparty.name if entry.local_counterparty else (
                        entry.legacy_counterparty_name or (
                            entry.contact.display_name if entry.contact else ""
                        )
                    )
                ),
                entry.get_entry_type_display(), entry.amount, entry.balance_delta,
                entry.balance_after, entry.currency, entry.description, entry.reference,
            )
            for entry in queryset.select_related("business", "counterparty_business", "local_counterparty", "contact")
            .order_by("occurred_on", "pk")
        )
        return csv_response(
            "ledger-entries.csv",
            ("تاریخ", "کسب‌وکار", "طرف حساب", "نوع", "مبلغ", "تغییر مانده", "مانده پس از ثبت", "ارز", "شرح", "مرجع"),
            rows,
        )

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
