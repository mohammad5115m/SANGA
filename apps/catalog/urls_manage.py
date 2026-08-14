from __future__ import annotations

from django.urls import path

from . import views_manage

app_name = "catalog_manage"

urlpatterns = [
    path("", views_manage.catalog_list, name="list"),
    path("new/", views_manage.catalog_create, name="create"),
    path("<uuid:catalog_id>/", views_manage.catalog_detail, name="detail"),
    path("<uuid:catalog_id>/remove-item/", views_manage.catalog_remove_item, name="remove_item"),
    path("<uuid:catalog_id>/toggle/", views_manage.catalog_toggle_active, name="toggle_active"),
    path("<uuid:catalog_id>/delete/", views_manage.catalog_delete, name="delete"),
    path("<uuid:catalog_id>/edit/", views_manage.catalog_edit, name="edit"),
]
