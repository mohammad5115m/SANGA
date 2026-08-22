from django.contrib import admin

from apps.core.admin import HistoricalRecordAdmin

from .models import BusinessInvoiceSettings, InvoiceTemplate, SalesInvoice, SalesInvoiceItem


class SalesInvoiceItemInline(admin.TabularInline):
    model = SalesInvoiceItem
    extra = 0
    # Line snapshots are the document. Editing them here would rewrite history.
    readonly_fields = ("product_name", "stone_type", "grade", "quantity", "unit_price", "line_total")

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(SalesInvoice)
class SalesInvoiceAdmin(HistoricalRecordAdmin):
    """Drafts stay editable; issued and cancelled documents do not.

    An issued invoice has been sent to somebody, and a cancelled one is the
    record that it was voided. Editing either in admin would change what a
    counterparty was told without leaving a trace, and deleting one would take
    its number with it — the sequence never reuses a number precisely so that a
    gap means something.
    """

    list_display = ("number", "issue_date", "seller_business", "buyer_name", "total_amount", "status")
    list_filter = ("status", "counterparty_type", "currency")
    search_fields = ("number", "buyer_name", "seller_business__name", "customer_phone")
    readonly_fields = ("number", "total_amount", "buyer_name", "created_at")
    inlines = [SalesInvoiceItemInline]

    def is_final(self, obj) -> bool:
        return obj is not None and obj.status != SalesInvoice.Status.DRAFT


@admin.register(BusinessInvoiceSettings)
class BusinessInvoiceSettingsAdmin(admin.ModelAdmin):
    list_display = ("business", "legal_name", "palette", "updated_at")
    search_fields = ("business__name", "legal_name", "tax_id")


@admin.register(InvoiceTemplate)
class InvoiceTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "business", "updated_at")
    list_filter = ("business",)
    search_fields = ("name", "business__name")
    readonly_fields = ("created_at", "updated_at")
