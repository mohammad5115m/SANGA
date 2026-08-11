from django.contrib import admin

from .models import MatchResult


@admin.register(MatchResult)
class MatchResultAdmin(admin.ModelAdmin):
    list_display = ("purchase_request", "lot", "score", "notified", "created_at")
    list_filter = ("notified",)
    search_fields = ("purchase_request__title", "lot__lot_code")
