from __future__ import annotations

from django.urls import path

from . import views

app_name = "invoicing"

urlpatterns = [
    path("", views.invoice_list, name="list"),
    path("new/", views.invoice_create, name="create"),
    path("preview/", views.invoice_preview, name="preview"),
    path("settings/", views.invoice_settings, name="settings"),
    path("counterparties/links/", views.counterparty_links, name="counterparty_links"),
    path("counterparties/links/propose/", views.counterparty_link_propose, name="counterparty_link_propose"),
    path(
        "counterparties/links/<uuid:proposal_id>/decide/",
        views.counterparty_link_decide,
        name="counterparty_link_decide",
    ),
    path(
        "counterparties/links/<uuid:proposal_id>/cancel/",
        views.counterparty_link_cancel,
        name="counterparty_link_cancel",
    ),
    path("settings/personal-signature/", views.personal_signature_update, name="personal_signature"),
    path("settings/assets/<str:kind>/", views.invoice_asset, name="asset"),
    path(
        "revisions/<uuid:revision_id>/signatures/<str:kind>/",
        views.revision_signature_asset,
        name="revision_signature",
    ),
    path("templates/<uuid:template_id>/delete/", views.invoice_template_delete, name="template_delete"),
    path("<uuid:invoice_id>/", views.invoice_detail, name="detail"),
    path("<uuid:invoice_id>/edit/", views.invoice_edit, name="edit"),
    path("<uuid:invoice_id>/print/", views.invoice_print, name="print"),
    path("<uuid:invoice_id>/pdf/", views.invoice_pdf, name="pdf"),
    path("<uuid:invoice_id>/image/", views.invoice_image, name="image"),
    path("<uuid:invoice_id>/duplicate/", views.invoice_duplicate, name="duplicate"),
    path("<uuid:invoice_id>/save-template/", views.invoice_save_template, name="save_template"),
    path("<uuid:invoice_id>/issue/", views.invoice_issue, name="issue"),
    path("<uuid:invoice_id>/confirm/", views.invoice_confirm, name="confirm"),
    path("<uuid:invoice_id>/reject/", views.invoice_reject, name="reject"),
    path("<uuid:invoice_id>/cancel-pending/", views.invoice_cancel_pending, name="cancel_pending"),
    path("<uuid:invoice_id>/offline-confirm/", views.invoice_offline_confirm, name="offline_confirm"),
    path("cheques/<uuid:cheque_id>/status/", views.cheque_status_update, name="cheque_status"),
    path("<uuid:invoice_id>/cancel/", views.invoice_cancel, name="cancel"),
    path("<uuid:invoice_id>/delete/", views.invoice_delete_draft, name="delete_draft"),
    path(
        "<uuid:invoice_id>/replace/",
        views.invoice_create_replacement,
        name="create_replacement",
    ),
]
