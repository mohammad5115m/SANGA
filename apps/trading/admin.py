from django.contrib import admin

from apps.core.admin import HistoricalRecordAdmin

from .models import PurchaseRequest, Trade, TradeItem, TradeProposal, TradeProposalItem


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


class TradeItemInline(admin.TabularInline):
    """The sold lines, shown but never editable.

    A line is history in exactly the way its trade is: the ledger and the invoice
    were both derived from these numbers, so editing one here would leave three
    records of one sale disagreeing with nothing to reconcile them.
    """

    model = TradeItem
    extra = 0
    can_delete = False
    fields = ("product_name", "stone_type", "grade", "quantity", "unit", "unit_price", "line_total")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None) -> bool:
        return False


class TradeProposalItemInline(admin.TabularInline):
    model = TradeProposalItem
    extra = 0
    can_delete = False
    fields = ("product_name", "stone_type", "grade", "quantity", "unit", "unit_price", "line_total")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(TradeProposal)
class TradeProposalAdmin(HistoricalRecordAdmin):
    list_display = (
        "created_at",
        "seller_business",
        "buyer_business",
        "initiated_by_business",
        "status",
        "total_amount",
    )
    list_filter = ("status", "currency")
    search_fields = ("seller_business__name", "buyer_business__name", "note")
    inlines = [TradeProposalItemInline]

    def has_add_permission(self, request) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]


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
        "summary_label",
        "total_amount",
    )
    list_filter = ("counterparty_type", "currency")
    search_fields = ("product_name", "seller_business__name", "buyer_business__name", "customer_name")
    inlines = [TradeItemInline]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("items")

    def has_add_permission(self, request) -> bool:
        # A trade is a consequence of finalizing a sale, never something typed in.
        return False
