from __future__ import annotations

from django.urls import path

from . import views

app_name = "inquiries"

urlpatterns = [
    path("", views.lead_list, name="leads"),
    path("customers/<uuid:lead_id>/", views.lead_detail, name="lead_detail"),
    path("inquiries/", views.inquiry_inbox, name="inbox"),
    path("inquiries/<uuid:inquiry_id>/", views.inquiry_detail, name="detail"),
    path("inquiries/<uuid:inquiry_id>/status/", views.inquiry_set_status, name="set_status"),
]
