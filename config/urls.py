from __future__ import annotations

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("apps.core.urls")),
    path("", include("apps.catalog.urls_public")),
    path("auth/", include("apps.accounts.urls")),
    path("app/", include("apps.businesses.urls")),
    path("app/inventory/", include("apps.inventory.urls")),
    path("app/catalogs/", include("apps.catalog.urls_manage")),
    path("app/marketplace/", include("apps.marketplace.urls")),
    path("app/partners/", include("apps.partners.urls")),
    path("app/purchase-requests/", include("apps.purchase_requests.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
