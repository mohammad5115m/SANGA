from __future__ import annotations

from django.urls import path

from . import views

app_name = "contacts"

urlpatterns = [
    path("", views.contact_list, name="list"),
    path("new/", views.contact_create, name="create"),
    path("<uuid:contact_id>/", views.contact_detail, name="detail"),
    path("<uuid:contact_id>/edit/", views.contact_edit, name="edit"),
    path("<uuid:contact_id>/archive/", views.contact_archive, name="archive"),
]
