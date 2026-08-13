from django.contrib import admin

from .models import Application, InventoryLot, LotMedia, Product, VocabularyTerm
from .taxonomy import clear_cache


class LotMediaInline(admin.TabularInline):
    model = LotMedia
    extra = 0


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "sort_order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "code")
    prepopulated_fields = {"code": ("name",)}


@admin.register(VocabularyTerm)
class VocabularyTermAdmin(admin.ModelAdmin):
    """The platform's controlled discovery vocabulary.

    Editing a term changes how the next product saved is spelled, not how the
    existing ones are: a rename here is a decision about future data, and
    rewriting what sellers already entered is a migration somebody should look
    at, not a side effect of an admin save.
    """

    list_display = ("name", "kind", "sort_order", "is_active")
    list_filter = ("kind", "is_active")
    search_fields = ("name",)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        clear_cache()

    def delete_model(self, request, obj):
        super().delete_model(request, obj)
        clear_cache()


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("commercial_name", "business", "stone_type", "primary_color", "is_active")
    list_filter = ("is_active", "stone_type")
    search_fields = ("commercial_name", "slug", "business__name")
    filter_horizontal = ("applications",)


@admin.register(InventoryLot)
class InventoryLotAdmin(admin.ModelAdmin):
    list_display = (
        "lot_code",
        "business",
        "product",
        "status",
        "is_visible",
        "availability_status",
        "stock_mode",
        "available_sqm",
        "stock_expires_at",
        "deleted_at",
    )
    list_filter = ("status", "is_visible", "availability_status", "stock_mode", "is_urgent_sale")
    search_fields = ("lot_code", "product__commercial_name", "business__name", "public_token")
    readonly_fields = ("public_token", "stock_expires_at")
    inlines = [LotMediaInline]


@admin.register(LotMedia)
class LotMediaAdmin(admin.ModelAdmin):
    list_display = ("lot", "kind", "is_primary", "sort_order")
