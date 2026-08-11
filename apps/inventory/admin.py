from django.contrib import admin

from .models import InventoryLot, LotMedia, Product


class LotMediaInline(admin.TabularInline):
    model = LotMedia
    extra = 0


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("commercial_name", "business", "stone_type", "primary_color", "is_active")
    list_filter = ("is_active", "stone_type")
    search_fields = ("commercial_name", "slug", "business__name")


@admin.register(InventoryLot)
class InventoryLotAdmin(admin.ModelAdmin):
    list_display = (
        "lot_code",
        "business",
        "product",
        "status",
        "visibility",
        "available_sqm",
        "inventory_confirmed_at",
    )
    list_filter = ("status", "visibility", "is_urgent_sale")
    search_fields = ("lot_code", "product__commercial_name", "business__name")
    inlines = [LotMediaInline]


@admin.register(LotMedia)
class LotMediaAdmin(admin.ModelAdmin):
    list_display = ("lot", "kind", "is_primary", "sort_order")
