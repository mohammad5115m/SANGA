from __future__ import annotations

from django.urls import path

from . import views_manage

app_name = "catalog_manage"

urlpatterns = [
    path("", views_manage.catalog_list, name="list"),
    path(
        "storefront/token/regenerate/",
        views_manage.storefront_token_regenerate,
        name="storefront_token_regenerate",
    ),
    path("storefront/collections/new/", views_manage.collection_create, name="collection_create"),
    path("storefront/collections/<uuid:collection_id>/", views_manage.collection_edit, name="collection_edit"),
    path(
        "storefront/collections/<uuid:collection_id>/suggest/",
        views_manage.collection_suggest,
        name="collection_suggest",
    ),
    path("storefront/collections/<uuid:collection_id>/move/", views_manage.collection_move, name="collection_move"),
    path(
        "storefront/collections/<uuid:collection_id>/delete/",
        views_manage.collection_delete,
        name="collection_delete",
    ),
    path(
        "storefront/collections/<uuid:collection_id>/items/<uuid:item_id>/move/",
        views_manage.collection_item_move,
        name="collection_item_move",
    ),
    path("new/", views_manage.catalog_create, name="create"),
    path("<uuid:catalog_id>/", views_manage.catalog_detail, name="detail"),
    path("<uuid:catalog_id>/remove-item/", views_manage.catalog_remove_item, name="remove_item"),
    path("<uuid:catalog_id>/toggle/", views_manage.catalog_toggle_active, name="toggle_active"),
    path("<uuid:catalog_id>/delete/", views_manage.catalog_delete, name="delete"),
    path("<uuid:catalog_id>/edit/", views_manage.catalog_edit, name="edit"),
]
