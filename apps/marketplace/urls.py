from __future__ import annotations

from django.urls import path

from . import views

app_name = "marketplace"

urlpatterns = [
    path("", views.marketplace_home, name="home"),
    path("items/<uuid:lot_id>/", views.marketplace_lot_detail, name="lot_detail"),
]
