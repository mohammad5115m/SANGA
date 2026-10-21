from django.contrib import admin

from apps.core.admin import HistoricalRecordAdmin
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


class SalesInvoiceItemInline(admin.TabularInline):
    model = SalesInvoiceItem
    extra = 0
    # Line snapshots are the document. Editing them here would rewrite history.
    readonly_fields = ("product_name", "stone_type", "grade", "quantity", "unit_price", "line_total")

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


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
class SalesInvoiceAdmin(HistoricalRecordAdmin):
    """Drafts stay editable; issued and cancelled documents do not.

    An issued invoice has been sent to somebody, and a cancelled one is the
    record that it was voided. Editing either in admin would change what a
    counterparty was told without leaving a trace, and deleting one would take
    its number with it — the sequence never reuses a number precisely so that a
    gap means something.
    """

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


for immutable_model in (InvoiceRevision, SettlementEvent, ChequeEvent):
    admin.site.register(
        immutable_model,
        type(
            f"{immutable_model.__name__}Admin",
            (admin.ModelAdmin,),
            {
                "readonly_fields": tuple(field.name for field in immutable_model._meta.fields),
                "has_add_permission": lambda self, request: False,
                "has_delete_permission": lambda self, request, obj=None: False,
            },
        ),
    )

admin.site.register(LocalCounterparty)
admin.site.register(UserInvoiceSignature)
admin.site.register(ChequeReceivable)
admin.site.register(CounterpartyLinkProposal)
