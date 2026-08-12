from __future__ import annotations

from django.urls import path

from . import views

app_name = "accounting"

urlpatterns = [
    path("", views.ledger_index, name="index"),
    path("aging/", views.aging_report, name="aging"),
    path("contacts/<uuid:contact_id>/", views.statement, name="statement"),
    path("contacts/<uuid:contact_id>/add/", views.add_entry, name="add_entry"),
    path("contacts/<uuid:contact_id>/print/", views.print_statement, name="print"),
    path("entries/<uuid:entry_id>/reverse/", views.reverse_view, name="reverse"),
    # Optional ?offer=<uuid> pre-fills the screen from an accepted purchase offer.
    path("record-trade/", views.record_trade, name="record_trade"),
]
