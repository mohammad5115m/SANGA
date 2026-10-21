from __future__ import annotations

from collections.abc import Callable
from functools import wraps

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect

from .entitlements import EntitlementError, require_entitlement

type View = Callable[..., HttpResponse]


def business_login_required[F: View](view: F) -> F:
    @wraps(view)
    def _wrapped(request: HttpRequest, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        return view(request, *args, **kwargs)

    return _wrapped  # type: ignore[return-value]


def require_capability[F: View](capability: str) -> Callable[[F], F]:
    def decorator(view: F) -> F:
        @wraps(view)
        def _wrapped(request: HttpRequest, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            membership = getattr(request, "membership", None)
            if membership is None:
                return redirect("businesses:no_business")
            if not membership.has_capability(capability):
                messages.error(request, "دسترسی لازم برای این عملیات را ندارید.")
                return redirect("businesses:dashboard")
            return view(request, *args, **kwargs)

        return _wrapped  # type: ignore[return-value]

    return decorator


def require_business_entitlement[F: View](entitlement: str) -> Callable[[F], F]:
    """Block write endpoints when the tenant cannot use the paid capability.

    Template visibility is only a convenience.  This decorator is the HTTP
    boundary counterpart to the service-layer entitlement checks.
    """

    def decorator(view: F) -> F:
        @wraps(view)
        def _wrapped(request: HttpRequest, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            business = getattr(request, "business", None)
            if business is None:
                return redirect("businesses:no_business")
            try:
                require_entitlement(business, entitlement)
            except EntitlementError as exc:
                messages.error(request, exc.message)
                return redirect("businesses:dashboard")
            return view(request, *args, **kwargs)

        return _wrapped  # type: ignore[return-value]

    return decorator
