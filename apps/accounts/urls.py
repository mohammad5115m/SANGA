from __future__ import annotations

from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("register/", views.register_view, name="register"),
    path("register/submitted/", views.registration_submitted, name="registration_submitted"),
    path("password/change/", views.password_change, name="password_change"),
    path("verify/", views.verify_view, name="verify"),
    path("logout/", views.logout_view, name="logout"),
    path("profile/", views.profile_view, name="profile"),
]
