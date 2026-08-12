from __future__ import annotations

from django.urls import path

from . import views

app_name = "businesses"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("post-login/", views.post_login, name="post_login"),
    path("no-business/", views.no_business, name="no_business"),
    path("onboarding/profile/", views.onboarding_profile, name="onboarding_profile"),
    path("onboarding/done/", views.onboarding_done, name="onboarding_done"),
    path("settings/", views.settings_view, name="settings"),
    path("team/", views.team_list, name="team"),
    path("colleagues/", views.colleague_list, name="colleagues"),
    path("colleagues/<uuid:business_id>/", views.colleague_detail, name="colleague_detail"),
    path("switch/", views.switch_business, name="switch_business"),
]
