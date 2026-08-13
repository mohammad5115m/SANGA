from __future__ import annotations

from django.urls import path

from . import views_inquiry, views_public

app_name = "catalog"

urlpatterns = [
    # Login-free discovery across every eligible seller.
    path("search/", views_public.public_search, name="public_search"),
    # Multi-product inquiry: browse → select → review → identify → verify → save.
    path("select/<uuid:item_id>/", views_inquiry.selection_toggle, name="selection_toggle"),
    path("ask/<uuid:item_id>/", views_inquiry.inquiry_start, name="inquiry_start"),
    path("stock-inquiry/<uuid:item_id>/", views_inquiry.stock_inquiry, name="stock_inquiry"),
    path("inquiry/", views_inquiry.selection_review, name="inquiry_review"),
    path("inquiry/remove/<uuid:item_id>/", views_inquiry.selection_remove, name="selection_remove"),
    path("inquiry/identify/", views_inquiry.inquiry_identify, name="inquiry_identify"),
    path("inquiry/verify/", views_inquiry.inquiry_verify, name="inquiry_verify"),
    path("inquiry/done/", views_inquiry.inquiry_done, name="inquiry_done"),
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
