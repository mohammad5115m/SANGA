from __future__ import annotations

from django.urls import path

from . import views_public

app_name = "catalog"

urlpatterns = [
    path("s/<str:business_slug>/", views_public.storefront, name="storefront"),
    path("s/<str:business_slug>/lots/<uuid:lot_id>/", views_public.lot_detail, name="lot_detail"),
    path("s/<str:business_slug>/compare/", views_public.compare_view, name="compare"),
    path(
        "s/<str:business_slug>/lots/<uuid:lot_id>/compare/",
        views_public.compare_toggle,
        name="compare_toggle",
    ),
    path("c/<str:share_token>/", views_public.shared_catalog, name="shared_catalog"),
]
