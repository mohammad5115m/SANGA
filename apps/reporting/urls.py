from __future__ import annotations

from django.urls import path

from . import views

app_name = "reporting"

urlpatterns = [
    path("", views.report_view, name="index"),
    path("<slug:key>/", views.report_view, name="report"),
]
