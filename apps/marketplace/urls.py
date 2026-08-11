from __future__ import annotations

from django.urls import path

from . import views

app_name = "marketplace"

urlpatterns = [
    path("", views.marketplace_home, name="home"),
    path("lots/<uuid:lot_id>/", views.marketplace_lot_detail, name="lot_detail"),
    path("save-search/", views.save_current_search, name="save_search"),
    path("suppliers/<uuid:supplier_id>/follow/", views.follow_toggle, name="follow_toggle"),
]
