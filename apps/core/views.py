from __future__ import annotations

import logging

from django.db import connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render

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
    return render(request, "core/offline.html", status=503)
