from __future__ import annotations

from django.urls import path

from . import views

app_name = "accounting"

urlpatterns = [
    path("", views.ledger_index, name="index"),
    path("contacts/<uuid:contact_id>/", views.statement, name="statement"),
    path("contacts/<uuid:contact_id>/add/", views.add_entry, name="add_entry"),
    path("contacts/<uuid:contact_id>/print/", views.print_statement, name="print"),
    path("entries/<uuid:entry_id>/reverse/", views.reverse_view, name="reverse"),
    path(
        "reservations/<uuid:reservation_id>/record/",
        views.record_trade,
        name="record_trade",
    ),
]
