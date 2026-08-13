from django.contrib import admin

from apps.core.admin import HistoricalRecordAdmin

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

    def has_delete_permission(self, request, obj=None) -> bool:
        # A completed request has a Trade hanging off it, and a rejected one is
        # the record of a decision somebody made.
        return False


@admin.register(Trade)
class TradeAdmin(HistoricalRecordAdmin):
    """A Trade is finalized the moment it exists, so it is read-only from the start.

    Every trade already moved both parties' ledgers. Editing the amount here
    would leave the books saying one thing and the trade another, with nothing to
    reconcile them; deleting it would leave ledger entries pointing at a sale
    that never happened. Corrections go through reversal, which is visible.
    """

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

    def has_add_permission(self, request) -> bool:
        # A trade is a consequence of finalizing a sale, never something typed in.
        return False
