from __future__ import annotations

from django.urls import path

from . import views_public

app_name = "catalog"

urlpatterns = [
    # Login-free discovery across every eligible seller.
    path("search/", views_public.public_search, name="public_search"),
    # Per-product share link. Short and opaque because it gets pasted into
    # WhatsApp and Telegram; see InventoryLot.public_token.
    path("p/<str:public_token>/", views_public.shared_item, name="shared_item"),
    path("s/<str:business_slug>/", views_public.storefront, name="storefront"),
    path("s/<str:business_slug>/items/<uuid:lot_id>/", views_public.lot_detail, name="lot_detail"),
    path("s/<str:business_slug>/compare/", views_public.compare_view, name="compare"),
    path(
        "s/<str:business_slug>/items/<uuid:lot_id>/compare/",
        views_public.compare_toggle,
        name="compare_toggle",
    ),
    path("c/<str:share_token>/", views_public.shared_catalog, name="shared_catalog"),
]
