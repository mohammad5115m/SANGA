from django.contrib import admin

from .models import PurchaseOffer, PurchaseRequest


class PurchaseOfferInline(admin.TabularInline):
    model = PurchaseOffer
    extra = 0
    readonly_fields = ("created_at",)


@admin.register(PurchaseRequest)
class PurchaseRequestAdmin(admin.ModelAdmin):
    list_display = ("title", "business", "stone_type", "required_qty_sqm", "status", "created_at")
    list_filter = ("status", "stone_type")
    search_fields = ("title", "business__name", "destination_city")
    inlines = [PurchaseOfferInline]


@admin.register(PurchaseOffer)
class PurchaseOfferAdmin(admin.ModelAdmin):
    list_display = (
        "purchase_request",
        "seller_business",
        "unit_price",
        "offered_qty_sqm",
        "status",
        "created_at",
    )
    list_filter = ("status",)
