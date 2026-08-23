"""Partner inquiry history; financial finalization remains invoice-owned."""

from django.contrib import admin

from .models import PartnerInquiry, PartnerInquiryBatch, PartnerInquiryItem


class PartnerInquiryItemInline(admin.TabularInline):
    model = PartnerInquiryItem
    extra = 0
    readonly_fields = tuple(field.name for field in PartnerInquiryItem._meta.fields)


@admin.register(PartnerInquiry)
class PartnerInquiryAdmin(admin.ModelAdmin):
    list_display = ("sent_at", "buyer_business", "seller_business", "status")
    list_filter = ("status",)
    search_fields = ("buyer_business__name", "seller_business__name")
    inlines = [PartnerInquiryItemInline]


admin.site.register(PartnerInquiryBatch)
