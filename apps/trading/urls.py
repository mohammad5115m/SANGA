from __future__ import annotations

from django.urls import path

from . import views

app_name = "trading"

urlpatterns = [
    path("agreements/", views.proposal_list, name="proposal_list"),
    path("agreements/new/", views.proposal_create, name="proposal_create"),
    path("agreements/product-options/", views.proposal_product_options, name="proposal_product_options"),
    path("agreements/<uuid:proposal_id>/", views.proposal_detail, name="proposal_detail"),
    path("agreements/<uuid:proposal_id>/edit/", views.proposal_edit, name="proposal_edit"),
    path("agreements/<uuid:proposal_id>/submit/", views.proposal_submit, name="proposal_submit"),
    path("agreements/<uuid:proposal_id>/confirm/", views.proposal_confirm, name="proposal_confirm"),
    path("agreements/<uuid:proposal_id>/reject/", views.proposal_reject, name="proposal_reject"),
    path("agreements/<uuid:proposal_id>/cancel/", views.proposal_cancel, name="proposal_cancel"),
    path("received/", views.received_list, name="received_list"),
    path("received/<uuid:request_id>/", views.received_detail, name="received_detail"),
    path("received/<uuid:request_id>/finalize/", views.finalize, name="finalize"),
    path("sent/", views.sent_list, name="sent_list"),
    path("sent/<uuid:request_id>/", views.sent_detail, name="sent_detail"),
    path("request/<uuid:item_id>/", views.request_create, name="request_create"),
    path("direct-sale/", views.direct_sale, name="direct_sale"),
    path("trades/", views.trade_list, name="trade_list"),
    path("trades/<uuid:trade_id>/", views.trade_detail, name="trade_detail"),
    path("trades/<uuid:trade_id>/invoice/", views.trade_create_invoice, name="trade_create_invoice"),
]
