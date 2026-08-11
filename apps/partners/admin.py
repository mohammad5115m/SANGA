from django.contrib import admin

from .models import PartnerRelation, SavedSearch, SupplierFollow


@admin.register(PartnerRelation)
class PartnerRelationAdmin(admin.ModelAdmin):
    list_display = ("supplier_business", "partner_business", "status", "created_at", "decided_at")
    list_filter = ("status",)
    search_fields = ("supplier_business__name", "partner_business__name")


@admin.register(SupplierFollow)
class SupplierFollowAdmin(admin.ModelAdmin):
    list_display = ("follower_business", "supplier_business", "created_at")


@admin.register(SavedSearch)
class SavedSearchAdmin(admin.ModelAdmin):
    list_display = ("name", "business", "user", "notify_enabled", "updated_at")
