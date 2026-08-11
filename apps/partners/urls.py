from __future__ import annotations

from django.urls import path

from . import views

app_name = "partners"

urlpatterns = [
    path("", views.directory, name="directory"),
    path("incoming/", views.incoming, name="incoming"),
    path("request/<uuid:supplier_id>/", views.request_partner, name="request"),
    path("decide/<uuid:relation_id>/", views.decide, name="decide"),
    path("notifications/", views.notifications_list, name="notifications"),
]
