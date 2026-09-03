from __future__ import annotations

from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("calendar/month/", views.persian_calendar_month, name="calendar_month"),
    path("health/", views.health, name="health"),
    path("offline/", views.offline, name="offline"),
    path("sw.js", views.service_worker, name="service_worker"),
]
