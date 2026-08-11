from __future__ import annotations

from django.urls import path

from . import views

app_name = "inventory"

urlpatterns = [
    path("", views.lot_list, name="lot_list"),
    path("quick-add/", views.quick_add_start, name="quick_add_start"),
    path("quick-add/product/", views.quick_add_product, name="quick_add_product"),
    path("quick-add/details/", views.quick_add_details, name="quick_add_details"),
    path("quick-add/quantity/", views.quick_add_quantity, name="quick_add_quantity"),
    path("quick-add/media/", views.quick_add_media, name="quick_add_media"),
    path("quick-add/prices/", views.quick_add_prices, name="quick_add_prices"),
    path("quick-add/visibility/", views.quick_add_visibility, name="quick_add_visibility"),
    path("quick-add/review/", views.quick_add_review, name="quick_add_review"),
    path("lots/<uuid:lot_id>/", views.lot_detail, name="lot_detail"),
    path("lots/<uuid:lot_id>/edit/", views.lot_edit, name="lot_edit"),
    path("lots/<uuid:lot_id>/confirm/", views.lot_confirm, name="lot_confirm"),
    path("lots/<uuid:lot_id>/duplicate/", views.lot_duplicate, name="lot_duplicate"),
    path("lots/<uuid:lot_id>/sold/", views.lot_mark_sold, name="lot_mark_sold"),
    path("lots/<uuid:lot_id>/hide/", views.lot_hide, name="lot_hide"),
    path("lots/<uuid:lot_id>/archive/", views.lot_archive, name="lot_archive"),
]
