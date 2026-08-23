from __future__ import annotations

from django.urls import path

from . import views

app_name = "marketplace"

urlpatterns = [
    path("", views.marketplace_home, name="home"),
    path(
        "shared/<str:public_token>/",
        views.marketplace_shared_item,
        name="shared_item",
    ),
    # Kept read-only so old notifications and invoice history do not break.
    path(
        "inquiries/<uuid:inquiry_id>/",
        views.archived_inquiry_detail,
        name="inquiry_detail",
    ),
    path("items/<uuid:lot_id>/", views.marketplace_lot_detail, name="lot_detail"),
]
