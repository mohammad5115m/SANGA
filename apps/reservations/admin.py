from __future__ import annotations

from django.contrib import admin

from .models import Reservation


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "lot",
        "seller_business",
        "requester_business",
        "quantity_sqm",
        "status",
        "expires_at",
        "created_at",
    )
    list_filter = ("status",)
    search_fields = ("lot__lot_code", "seller_business__name", "requester_business__name")
    raw_id_fields = ("lot", "seller_business", "requester_business", "source_offer")
    readonly_fields = ("created_at", "updated_at", "released_at", "decided_at")
