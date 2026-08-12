from django.contrib import admin

from .models import SalesInvoice, SalesInvoiceItem


class SalesInvoiceItemInline(admin.TabularInline):
    model = SalesInvoiceItem
    extra = 0
    # Line snapshots are the document. Editing them here would rewrite history.
    readonly_fields = ("product_name", "stone_type", "grade", "quantity", "unit_price", "line_total")


@admin.register(SalesInvoice)
class SalesInvoiceAdmin(admin.ModelAdmin):
    list_display = ("number", "issue_date", "seller_business", "buyer_name", "total_amount", "status")
    list_filter = ("status", "counterparty_type", "currency")
    search_fields = ("number", "buyer_name", "seller_business__name", "customer_phone")
    readonly_fields = ("number", "total_amount", "buyer_name", "created_at")
    inlines = [SalesInvoiceItemInline]
