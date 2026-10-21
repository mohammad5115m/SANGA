from __future__ import annotations

from django.urls import path

from . import views_inquiry, views_public

app_name = "catalog"

urlpatterns = [
    # Per-product share link. Short and opaque because it gets pasted into
    # WhatsApp and Telegram; see InventoryLot.public_token.
    path("p/<str:public_token>/", views_public.shared_item, name="shared_item"),
    path("store/<str:storefront_token>/", views_public.storefront, name="storefront"),
    path("store/<str:storefront_token>/items/<uuid:lot_id>/", views_public.lot_detail, name="lot_detail"),
    path(
        "store/<str:storefront_token>/select/<uuid:item_id>/",
        views_inquiry.selection_toggle,
        name="selection_toggle",
    ),
    path("store/<str:storefront_token>/ask/<uuid:item_id>/", views_inquiry.inquiry_start, name="inquiry_start"),
    path(
        "store/<str:storefront_token>/stock-inquiry/<uuid:item_id>/",
        views_inquiry.stock_inquiry,
        name="stock_inquiry",
    ),
    path("store/<str:storefront_token>/inquiry/", views_inquiry.selection_review, name="inquiry_review"),
    path(
        "store/<str:storefront_token>/inquiry/remove/<uuid:item_id>/",
        views_inquiry.selection_remove,
        name="selection_remove",
    ),
    path("store/<str:storefront_token>/inquiry/identify/", views_inquiry.inquiry_identify, name="inquiry_identify"),
    path("store/<str:storefront_token>/inquiry/verify/", views_inquiry.inquiry_verify, name="inquiry_verify"),
    path("store/<str:storefront_token>/inquiry/done/", views_inquiry.inquiry_done, name="inquiry_done"),
    path("c/<str:share_token>/", views_public.shared_catalog, name="shared_catalog"),
]
