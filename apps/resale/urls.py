from django.urls import path

from . import public, views

app_name = "resale"

urlpatterns = [
    path("app/resale/", views.index, name="index"),
    path("app/resale/warehouse/", views.warehouse, name="warehouse"),
    path("app/resale/warehouse/enable/", views.enable, name="enable"),
    path("app/resale/warehouse/grant/", views.grant, name="grant"),
    path("app/resale/warehouse/grant/<int:grant_id>/revoke/", views.revoke, name="revoke"),
    path("app/resale/warehouse/prices/<uuid:lot_id>/", views.warehouse_price, name="warehouse_price"),
    path("app/resale/warehouse/requests/", views.warehouse_requests, name="warehouse_requests"),
    path("app/resale/warehouse/requests/<int:order_id>/invoice/", views.wholesale_invoice, name="wholesale_invoice"),
    path("app/resale/offer/<int:rule_id>/", views.offer_edit, name="offer_edit"),
    path("app/resale/offer/<uuid:offer_id>/stop/", views.offer_stop, name="offer_stop"),
    path("app/resale/requests/<int:order_id>/purchase/", views.purchase, name="purchase"),
    path("app/resale/requests/<int:order_id>/invoice/", views.retail_invoice, name="retail_invoice"),
    path("offers/<str:token>/", public.public_store, name="public_store"),
    path("offers/<str:token>/<uuid:offer_id>/ask/", public.ask, name="ask"),
    path("offers/<str:token>/<uuid:offer_id>/verify/", public.verify, name="verify"),
]
