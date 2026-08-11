from django.contrib import admin

from .models import Business, BusinessMembership, Warehouse


class WarehouseInline(admin.TabularInline):
    model = Warehouse
    extra = 0


class MembershipInline(admin.TabularInline):
    model = BusinessMembership
    extra = 0
    autocomplete_fields = ("user",)


@admin.register(Business)
class BusinessAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "city", "verification_status", "status", "created_at")
    list_filter = ("verification_status", "status")
    search_fields = ("name", "slug", "phone", "city")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [WarehouseInline, MembershipInline]


@admin.register(BusinessMembership)
class BusinessMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "business", "role", "status", "joined_at")
    list_filter = ("role", "status")
    search_fields = ("user__phone", "user__full_name", "business__name")
    autocomplete_fields = ("user", "business")


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ("name", "business", "city", "is_default", "is_active")
    list_filter = ("is_active", "is_default")
    search_fields = ("name", "business__name", "city")
