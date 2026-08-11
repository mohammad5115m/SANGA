from __future__ import annotations

from django.urls import path

from . import views

app_name = "businesses"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("post-login/", views.post_login, name="post_login"),
    path("onboarding/", views.onboarding_start, name="onboarding_start"),
    path("onboarding/warehouse/", views.onboarding_warehouse, name="onboarding_warehouse"),
    path("onboarding/profile/", views.onboarding_profile, name="onboarding_profile"),
    path("onboarding/done/", views.onboarding_done, name="onboarding_done"),
    path("settings/", views.settings_view, name="settings"),
    path("warehouses/", views.warehouse_list, name="warehouses"),
    path("team/", views.team_list, name="team"),
    path("switch/", views.switch_business, name="switch_business"),
]
