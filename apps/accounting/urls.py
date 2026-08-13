from __future__ import annotations

from django.urls import path

from . import views

app_name = "accounting"

urlpatterns = [
    path("", views.ledger_index, name="index"),
    path("aging/", views.aging_report, name="aging"),
    path("legacy/", views.legacy_list, name="legacy"),
    path("colleagues/<uuid:business_id>/", views.statement, name="statement"),
    path("colleagues/<uuid:business_id>/add/", views.add_entry, name="add_entry"),
    path("colleagues/<uuid:business_id>/print/", views.print_statement, name="print"),
    path("entries/<uuid:entry_id>/reverse/", views.reverse_view, name="reverse"),
]
