from __future__ import annotations

from django.urls import path

from . import views

app_name = "invoicing"

urlpatterns = [
    path("", views.invoice_list, name="list"),
    path("new/", views.invoice_create, name="create"),
    path("preview/", views.invoice_preview, name="preview"),
    path("settings/", views.invoice_settings, name="settings"),
    path("settings/assets/<str:kind>/", views.invoice_asset, name="asset"),
    path("templates/<uuid:template_id>/delete/", views.invoice_template_delete, name="template_delete"),
    path("<uuid:invoice_id>/", views.invoice_detail, name="detail"),
    path("<uuid:invoice_id>/edit/", views.invoice_edit, name="edit"),
    path("<uuid:invoice_id>/print/", views.invoice_print, name="print"),
    path("<uuid:invoice_id>/pdf/", views.invoice_pdf, name="pdf"),
    path("<uuid:invoice_id>/image/", views.invoice_image, name="image"),
    path("<uuid:invoice_id>/duplicate/", views.invoice_duplicate, name="duplicate"),
    path("<uuid:invoice_id>/save-template/", views.invoice_save_template, name="save_template"),
    path("<uuid:invoice_id>/issue/", views.invoice_issue, name="issue"),
    path("<uuid:invoice_id>/cancel/", views.invoice_cancel, name="cancel"),
    path("<uuid:invoice_id>/delete/", views.invoice_delete_draft, name="delete_draft"),
    path(
        "<uuid:invoice_id>/replace/",
        views.invoice_create_replacement,
        name="create_replacement",
    ),
]
