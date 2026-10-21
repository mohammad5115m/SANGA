from __future__ import annotations

from django.urls import path

from . import views

app_name = "inquiries"

urlpatterns = [
    path("", views.lead_list, name="leads"),
    path("customers/<uuid:lead_id>/", views.lead_detail, name="lead_detail"),
    path(
        "customers/<uuid:lead_id>/profile/",
        views.lead_update_profile,
        name="lead_update_profile",
    ),
    path("customers/<uuid:lead_id>/notes/", views.lead_add_note, name="lead_add_note"),
    path(
        "customers/<uuid:lead_id>/followups/schedule/",
        views.lead_schedule_followup,
        name="lead_schedule_followup",
    ),
    path(
        "customers/<uuid:lead_id>/followups/record/",
        views.lead_record_followup,
        name="lead_record_followup",
    ),
    path("followups/", views.followup_list, name="followups"),
    path(
        "followups/<uuid:followup_id>/complete/",
        views.followup_complete,
        name="followup_complete",
    ),
    path(
        "followups/<uuid:followup_id>/postpone/",
        views.followup_postpone,
        name="followup_postpone",
    ),
    path(
        "followups/<uuid:followup_id>/reschedule/",
        views.followup_reschedule,
        name="followup_reschedule",
    ),
    path(
        "followups/<uuid:followup_id>/cancel/",
        views.followup_cancel,
        name="followup_cancel",
    ),
    path("inquiries/", views.inquiry_inbox, name="inbox"),
    path("inquiries/<uuid:inquiry_id>/", views.inquiry_detail, name="detail"),
    path("inquiries/<uuid:inquiry_id>/status/", views.inquiry_set_status, name="set_status"),
]
