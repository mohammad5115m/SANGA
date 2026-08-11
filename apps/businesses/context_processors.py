from __future__ import annotations

from django.http import HttpRequest


def business_context(request: HttpRequest) -> dict:
    return {
        "current_business": getattr(request, "business", None),
        "current_membership": getattr(request, "membership", None),
        "user_memberships": getattr(request, "user_memberships", []),
    }
