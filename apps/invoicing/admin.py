from django.contrib import admin

from apps.core.admin import ReadOnlyAdmin, ReadOnlyTabularInline
from apps.core.admin_export import csv_response

from .models import (
    BusinessInvoiceSettings,
    ChequeEvent,
    ChequeReceivable,
    CounterpartyLinkProposal,
    InvoiceRevision,
    InvoiceTemplate,
    LocalCounterparty,
    SalesInvoice,
    SalesInvoiceItem,
    SettlementEvent,
    UserInvoiceSignature,
)


class SalesInvoiceItemInline(ReadOnlyTabularInline):
    model = SalesInvoiceItem
    fields = ("product_name", "description", "quantity", "unit", "unit_price", "discount_amount", "line_total")


class InvoiceStatusFilter(admin.ChoicesFieldListFilter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.title = "وضعیت"


class InvoiceCounterpartyFilter(admin.ChoicesFieldListFilter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.title = "نوع خریدار"


class InvoiceCurrencyFilter(admin.ChoicesFieldListFilter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.title = "ارز"


@admin.register(SalesInvoice)
class SalesInvoiceAdmin(ReadOnlyAdmin):
    """Even drafts use the invoice editor for totals, revisions and transitions."""

    list_display = ("number", "issue_date", "seller_business", "buyer_name", "total_amount", "status")
    list_filter = (
        ("status", InvoiceStatusFilter),
        ("counterparty_type", InvoiceCounterpartyFilter),
        ("currency", InvoiceCurrencyFilter),
    )
    search_fields = ("number", "buyer_name", "seller_business__name", "customer_phone")
    readonly_fields = ("number", "total_amount", "buyer_name", "created_at")
    inlines = [SalesInvoiceItemInline]
    actions = ["export_selected_csv"]

    def has_add_permission(self, request) -> bool:
        # Invoice creation belongs to the sales flow so its line snapshots,
        # totals, and confirmation state are built together.
        return False

    def changelist_view(self, request, extra_context=None):
        return super().changelist_view(
            request, {"title": "فاکتورهای فروش", **(extra_context or {})},
        )

    @admin.action(description="دریافت CSV فاکتورهای انتخاب‌شده", permissions=["view"])
    def export_selected_csv(self, request, queryset):
        rows = (
            (
                invoice.number, invoice.issue_date, invoice.seller_business.name,
                invoice.buyer_name, invoice.get_status_display(), invoice.total_amount,
                invoice.currency,
            )
            for invoice in queryset.select_related("seller_business").order_by("issue_date", "pk")
        )
        return csv_response(
            "sales-invoices.csv",
            ("شماره", "تاریخ صدور", "فروشنده", "خریدار", "وضعیت", "مبلغ کل", "ارز"),
            rows,
        )

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


for service_owned_model in (
    InvoiceRevision, SettlementEvent, ChequeEvent, ChequeReceivable,
    LocalCounterparty, CounterpartyLinkProposal, UserInvoiceSignature,
):
    admin.site.register(service_owned_model, ReadOnlyAdmin)
