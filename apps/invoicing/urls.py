from __future__ import annotations

from django.urls import path

from . import views

app_name = "invoicing"

urlpatterns = [
    path("", views.invoice_list, name="list"),
    path("new/", views.invoice_create, name="create"),
    path("<uuid:invoice_id>/", views.invoice_detail, name="detail"),
    path("<uuid:invoice_id>/print/", views.invoice_print, name="print"),
    path("<uuid:invoice_id>/issue/", views.invoice_issue, name="issue"),
    path("<uuid:invoice_id>/cancel/", views.invoice_cancel, name="cancel"),
]
