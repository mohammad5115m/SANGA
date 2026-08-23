from __future__ import annotations

from django.urls import path

from . import views

app_name = "marketplace"

urlpatterns = [
    path("", views.marketplace_home, name="home"),
    path("inquiries/", views.inquiry_list, name="inquiries"),
    path("inquiries/create/", views.inquiry_create, name="inquiry_create"),
    path("inquiries/<uuid:inquiry_id>/", views.inquiry_detail, name="inquiry_detail"),
    path("inquiries/<uuid:inquiry_id>/convert/", views.inquiry_convert, name="inquiry_convert"),
    path("items/<uuid:lot_id>/", views.marketplace_lot_detail, name="lot_detail"),
]
