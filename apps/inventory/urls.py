from __future__ import annotations

from django.urls import path

from . import views

app_name = "inventory"

urlpatterns = [
    path("", views.lot_list, name="lot_list"),
    path("quick-add/", views.quick_add_start, name="quick_add_start"),
    path("quick-add/product/", views.quick_add_product, name="quick_add_product"),
    path("quick-add/details/", views.quick_add_details, name="quick_add_details"),
    path("quick-add/stock/", views.quick_add_stock, name="quick_add_stock"),
    path("quick-add/review/", views.quick_add_review, name="quick_add_review"),
    path("items/<uuid:lot_id>/", views.lot_detail, name="lot_detail"),
    path("items/<uuid:lot_id>/edit/", views.lot_edit, name="lot_edit"),
    path("items/<uuid:lot_id>/media/", views.lot_media, name="lot_media"),
    path("items/<uuid:lot_id>/confirm-stock/", views.lot_confirm_stock, name="lot_confirm_stock"),
    path("items/<uuid:lot_id>/availability/", views.lot_set_availability, name="lot_set_availability"),
    path("items/<uuid:lot_id>/visibility/", views.lot_set_visibility, name="lot_set_visibility"),
    path("items/<uuid:lot_id>/duplicate/", views.lot_duplicate, name="lot_duplicate"),
    path("items/<uuid:lot_id>/delete/", views.lot_delete, name="lot_delete"),
]
