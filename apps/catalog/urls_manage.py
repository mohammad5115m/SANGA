from __future__ import annotations

from django.urls import path

from . import views_manage

app_name = "catalog_manage"

urlpatterns = [
    path("", views_manage.catalog_list, name="list"),
    path("new/", views_manage.catalog_create, name="create"),
    path("<uuid:catalog_id>/", views_manage.catalog_detail, name="detail"),
    path("<uuid:catalog_id>/edit/", views_manage.catalog_edit, name="edit"),
    path("inquiries/", views_manage.inquiry_inbox, name="inquiries"),
]
