from __future__ import annotations

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import HttpResponseNotFound
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
    path("app/notifications/", include("apps.notifications.urls")),
    path("app/trading/", include("apps.trading.urls")),
    path("app/accounting/", include("apps.accounting.urls")),
    path("app/invoices/", include("apps.invoicing.urls")),
    path("app/leads/", include("apps.inquiries.urls")),
    path("app/reports/", include("apps.reporting.urls")),
]

if settings.DEBUG:
    # Invoice branding/signatures are private even in development. They are
    # streamed through tenant-authorized invoicing views and must not fall
    # through to Django's public DEBUG media helper.
    urlpatterns += [
        path(
            "media/invoice-assets/<path:asset_path>",
            lambda request, asset_path: HttpResponseNotFound(),
        )
    ]
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
