from __future__ import annotations

from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("", views.notifications_list, name="list"),
    path("read-all/", views.notifications_read_all, name="read_all"),
    path("<str:source>/<uuid:notification_id>/read/", views.notification_read, name="read"),
]
