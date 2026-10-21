"""Read-only admin for the retired demand board.

Registered so historical rows stay inspectable — ledger entries still reference
``PurchaseOffer`` — but not editable: the workflow that produced them no longer
exists, so a half-edited legacy offer would mean nothing.
"""

from django.contrib import admin

from .models import PurchaseOffer, PurchaseRequest


class _ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PurchaseRequest)
class LegacyPurchaseRequestAdmin(_ReadOnlyAdmin):
    list_display = ("title", "business", "stone_type", "required_qty_sqm", "status", "created_at")
    list_filter = ("status", "stone_type")
    search_fields = ("title", "business__name", "destination_city")


@admin.register(PurchaseOffer)
class LegacyPurchaseOfferAdmin(_ReadOnlyAdmin):
    list_display = (
        "purchase_request",
        "seller_business",
        "unit_price",
        "offered_qty_sqm",
        "status",
        "created_at",
    )
    list_filter = ("status",)
