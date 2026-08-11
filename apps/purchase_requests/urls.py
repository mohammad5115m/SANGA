from __future__ import annotations

from django.urls import path

from . import views

app_name = "purchase_requests"

urlpatterns = [
    path("", views.my_list, name="my_list"),
    path("network/", views.network_list, name="network_list"),
    path("new/", views.create, name="create"),
    path("<uuid:pr_id>/", views.detail, name="detail"),
    path("<uuid:pr_id>/rematch/", views.rematch, name="rematch"),
    path("<uuid:pr_id>/close/", views.close, name="close"),
    path("network/<uuid:pr_id>/", views.network_detail, name="network_detail"),
    path("offers/<uuid:offer_id>/decide/", views.offer_decide, name="offer_decide"),
]
