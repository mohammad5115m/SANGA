from django.contrib import admin

from apps.core.admin import ReadOnlyAdmin

from .models import LotPrice, PriceTier


@admin.register(PriceTier)
class PriceTierAdmin(ReadOnlyAdmin):
    list_display = ("code", "name", "is_active", "sort_order")


@admin.register(LotPrice)
class LotPriceAdmin(ReadOnlyAdmin):
    list_display = ("lot", "tier", "mode", "amount", "special_amount", "price_expires_at", "updated_at")
    list_filter = ("tier", "mode", "currency")
    readonly_fields = ("price_expires_at",)
