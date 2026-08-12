from django.contrib import admin

from .models import PurchaseRequest, Trade


@admin.register(PurchaseRequest)
class PurchaseRequestAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "buyer_business",
        "seller_business",
        "item",
        "requested_qty_sqm",
        "status",
    )
    list_filter = ("status",)
    search_fields = ("buyer_business__name", "seller_business__name", "item__lot_code")
    autocomplete_fields = ("item", "buyer_business", "seller_business")


@admin.register(Trade)
class TradeAdmin(admin.ModelAdmin):
    list_display = (
        "finalized_at",
        "seller_business",
        "counterparty_label",
        "product_name",
        "quantity_sqm",
        "total_amount",
    )
    list_filter = ("counterparty_type", "currency")
    search_fields = ("product_name", "seller_business__name", "buyer_business__name", "customer_name")
    # Snapshot columns are history: editing them in admin would rewrite what was
    # sold, which is exactly what the snapshot exists to prevent.
    readonly_fields = (
        "product_name",
        "stone_type",
        "grade",
        "quantity_sqm",
        "unit_price",
        "total_amount",
        "finalized_at",
    )
