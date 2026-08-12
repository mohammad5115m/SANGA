from __future__ import annotations

from django.http import HttpRequest

from .permissions import ALL_CAPABILITIES


def business_context(request: HttpRequest) -> dict:
    membership = getattr(request, "membership", None)
    return {
        "current_business": getattr(request, "business", None),
        "current_membership": membership,
        "user_memberships": getattr(request, "user_memberships", []),
        # Codes this member actually holds, so navigation can hide links that
        # would only end in «دسترسی ندارید». Derived from has_capability, so
        # owner bypass and suspended memberships behave identically to the
        # server-side checks. Never a substitute for them.
        "capabilities": _capability_codes(membership),
    }


def _capability_codes(membership) -> frozenset[str]:
    if membership is None:
        return frozenset()
    return frozenset(code for code in ALL_CAPABILITIES if membership.has_capability(code))
