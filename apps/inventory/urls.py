from __future__ import annotations

from django.urls import path

from . import views

app_name = "inventory"

urlpatterns = [
    path("", views.lot_list, name="lot_list"),
    path("new/", views.product_create, name="product_create"),
    path("quick-add/", views.product_create, name="quick_add_start"),
    path("product-options/", views.product_options, name="product_options"),
    path("catalog-selection/", views.catalog_selection_start, name="catalog_selection_start"),
    path("items/<uuid:lot_id>/", views.lot_detail, name="lot_detail"),
    path("items/<uuid:lot_id>/edit/", views.lot_edit, name="lot_edit"),
    path("items/<uuid:lot_id>/media/", views.lot_media, name="lot_media"),
    path("items/<uuid:lot_id>/confirm-stock/", views.lot_confirm_stock, name="lot_confirm_stock"),
    path("items/<uuid:lot_id>/availability/", views.lot_set_availability, name="lot_set_availability"),
    path("items/<uuid:lot_id>/visibility/", views.lot_set_visibility, name="lot_set_visibility"),
    path("items/<uuid:lot_id>/duplicate/", views.lot_duplicate, name="lot_duplicate"),
    path("items/<uuid:lot_id>/delete/", views.lot_delete, name="lot_delete"),
]
