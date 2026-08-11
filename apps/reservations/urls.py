from __future__ import annotations

from django.urls import path

from . import views

app_name = "reservations"

urlpatterns = [
    path("", views.inbox, name="inbox"),
    path("mine/", views.my_list, name="my_list"),
    path("lots/<uuid:lot_id>/request/", views.request_create, name="request_create"),
    path("<uuid:reservation_id>/", views.detail, name="detail"),
    path("<uuid:reservation_id>/approve/", views.approve, name="approve"),
    path("<uuid:reservation_id>/reject/", views.reject, name="reject"),
    path("<uuid:reservation_id>/extend/", views.extend, name="extend"),
    path("<uuid:reservation_id>/cancel/", views.cancel, name="cancel"),
    path("<uuid:reservation_id>/convert/", views.convert, name="convert"),
]
