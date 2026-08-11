from django.contrib import admin

from .models import LotPrice, PriceTier


@admin.register(PriceTier)
class PriceTierAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active", "sort_order")


@admin.register(LotPrice)
class LotPriceAdmin(admin.ModelAdmin):
    list_display = ("lot", "tier", "amount", "currency", "unit", "updated_at")
    list_filter = ("tier", "currency", "unit")
