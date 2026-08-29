from __future__ import annotations

import logging

from django.conf import settings
from django.db import connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import cache_control

logger = logging.getLogger(__name__)


def home(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("businesses:dashboard")
    return redirect("accounts:login")


def health(request: HttpRequest) -> JsonResponse:
    """Liveness/readiness style health endpoint for Docker/load balancers."""
    status = {"status": "ok", "database": "ok"}
    http_status = 200
    try:
        connection.ensure_connection()
    except Exception:
        logger.exception("Health check database failure")
        status["status"] = "degraded"
        status["database"] = "error"
        http_status = 503
    return JsonResponse(status, status=http_status)


def offline(request: HttpRequest) -> HttpResponse:
    # Service-worker cache.addAll() only accepts successful responses. Keep the
    # offline document cacheable; it is merely fallback content, not the failed
    # network response itself.
    return render(request, "core/offline.html")


@cache_control(max_age=0, must_revalidate=True)
def service_worker(request: HttpRequest) -> HttpResponse:
    """Serve the service worker from the site root so its scope covers all pages.

    Registering it from /static/js/ would restrict its scope to that path and
    it could never intercept navigations for offline support.
    """
    sw_path = settings.BASE_DIR / "static" / "js" / "sw.js"
    try:
        source = sw_path.read_text(encoding="utf-8")
    except OSError:
        logger.exception("Service worker file missing at %s", sw_path)
        return HttpResponse(status=404)
    return HttpResponse(source, content_type="application/javascript")
