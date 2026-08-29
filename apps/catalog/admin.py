from django.contrib import admin

from .models import (
    CustomCatalog,
    CustomCatalogItem,
    StorefrontCollection,
    StorefrontCollectionItem,
)


class CustomCatalogItemInline(admin.TabularInline):
    model = CustomCatalogItem
    extra = 0
    autocomplete_fields = ("lot",)


@admin.register(CustomCatalog)
class CustomCatalogAdmin(admin.ModelAdmin):
    list_display = ("title", "business", "customer_name", "is_active", "view_count", "created_at")
    list_filter = ("is_active",)
    search_fields = ("title", "share_token", "business__name", "customer_name")
    readonly_fields = ("share_token", "view_count", "first_viewed_at", "last_viewed_at")
    inlines = [CustomCatalogItemInline]


@admin.register(CustomCatalogItem)
class CustomCatalogItemAdmin(admin.ModelAdmin):
    list_display = ("catalog", "lot", "sort_order")


class StorefrontCollectionItemInline(admin.TabularInline):
    model = StorefrontCollectionItem
    extra = 0
    autocomplete_fields = ("lot",)


@admin.register(StorefrontCollection)
class StorefrontCollectionAdmin(admin.ModelAdmin):
    list_display = ("title", "business", "is_active", "sort_order", "suggestion_kind")
    list_filter = ("is_active", "suggestion_kind")
    search_fields = ("title", "business__name")
    inlines = [StorefrontCollectionItemInline]


@admin.register(StorefrontCollectionItem)
class StorefrontCollectionItemAdmin(admin.ModelAdmin):
    list_display = ("collection", "lot", "sort_order")
    list_filter = ("collection__business",)
