from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import TypeVar

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect

F = TypeVar("F", bound=Callable[..., HttpResponse])


def business_login_required(view: F) -> F:
    @wraps(view)
    def _wrapped(request: HttpRequest, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        return view(request, *args, **kwargs)

    return _wrapped  # type: ignore[return-value]


def require_capability(capability: str) -> Callable[[F], F]:
    def decorator(view: F) -> F:
        @wraps(view)
        def _wrapped(request: HttpRequest, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            membership = getattr(request, "membership", None)
            if membership is None:
                messages.info(request, "ابتدا کسب‌وکار خود را بسازید.")
                return redirect("businesses:onboarding_start")
            if not membership.has_capability(capability):
                messages.error(request, "دسترسی لازم برای این عملیات را ندارید.")
                return redirect("businesses:dashboard")
            return view(request, *args, **kwargs)

        return _wrapped  # type: ignore[return-value]

    return decorator
