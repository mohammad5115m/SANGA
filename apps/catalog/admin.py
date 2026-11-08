from django.contrib import admin

from apps.businesses.eligibility import business_can_sell, can_sell_q
from apps.businesses.models import Business
from apps.core.admin import FixedBusinessAdmin, NoDeleteAdmin, SameBusinessForm

from .models import (
    CustomCatalog,
    CustomCatalogItem,
    PublicHomepage,
    StorefrontCollection,
    StorefrontCollectionItem,
)


@admin.register(PublicHomepage)
class PublicHomepageAdmin(NoDeleteAdmin):
    fields = ("business", "public_status")
    readonly_fields = ("public_status",)
    list_display = ("__str__", "business", "public_status")
    list_select_related = ("business",)

    def has_add_permission(self, request):
        return False  # Migration provisions the singleton without a seller.

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "business":
            kwargs["queryset"] = Business.objects.filter(can_sell_q())
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @admin.display(description="وضعیت نمایش عمومی")
    def public_status(self, obj):
        if obj is None or obj.business_id is None:
            return "هنوز ویترینی انتخاب نشده است."
        if not business_can_sell(obj.business):
            return f"ویترین «{obj.business}» فعلاً واجد شرایط نمایش نیست."
        return "ویترین انتخاب‌شده در صفحهٔ اصلی فعال است."


class CatalogItemAdminForm(SameBusinessForm):
    business_relations = (("catalog", "business_id"), ("lot", "business_id"))


class CollectionItemAdminForm(SameBusinessForm):
    business_relations = (("collection", "business_id"), ("lot", "business_id"))


class CustomCatalogItemInline(admin.TabularInline):
    model = CustomCatalogItem
    form = CatalogItemAdminForm
    extra = 0
    autocomplete_fields = ("lot",)


@admin.register(CustomCatalog)
class CustomCatalogAdmin(FixedBusinessAdmin):
    list_display = ("title", "business", "customer_name", "is_active", "view_count", "created_at")
    list_filter = ("is_active",)
    search_fields = ("title", "share_token", "business__name", "customer_name")
    readonly_fields = ("share_token", "view_count", "first_viewed_at", "last_viewed_at")
    inlines = [CustomCatalogItemInline]


@admin.register(CustomCatalogItem)
class CustomCatalogItemAdmin(admin.ModelAdmin):
    form = CatalogItemAdminForm
    list_display = ("catalog", "lot", "sort_order")


class StorefrontCollectionItemInline(admin.TabularInline):
    model = StorefrontCollectionItem
    form = CollectionItemAdminForm
    extra = 0
    autocomplete_fields = ("lot",)


@admin.register(StorefrontCollection)
class StorefrontCollectionAdmin(FixedBusinessAdmin):
    list_display = ("title", "business", "is_active", "sort_order", "suggestion_kind")
    list_filter = ("is_active", "suggestion_kind")
    search_fields = ("title", "business__name")
    inlines = [StorefrontCollectionItemInline]


@admin.register(StorefrontCollectionItem)
class StorefrontCollectionItemAdmin(admin.ModelAdmin):
    form = CollectionItemAdminForm
    list_display = ("collection", "lot", "sort_order")
    list_filter = ("collection__business",)
