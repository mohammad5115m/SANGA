from __future__ import annotations

from django.urls import path

from . import views

app_name = "trading"

urlpatterns = [
    path("received/", views.received_list, name="received_list"),
    path("received/<uuid:request_id>/", views.received_detail, name="received_detail"),
    path("received/<uuid:request_id>/finalize/", views.finalize, name="finalize"),
    path("sent/", views.sent_list, name="sent_list"),
    path("sent/<uuid:request_id>/", views.sent_detail, name="sent_detail"),
    path("request/<uuid:item_id>/", views.request_create, name="request_create"),
    path("direct-sale/", views.direct_sale, name="direct_sale"),
    path("trades/", views.trade_list, name="trade_list"),
    path("trades/<uuid:trade_id>/", views.trade_detail, name="trade_detail"),
]
