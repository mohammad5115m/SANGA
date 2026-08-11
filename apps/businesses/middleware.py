from __future__ import annotations

from typing import Callable

from django.http import HttpRequest, HttpResponse

from .selectors import get_active_membership, memberships_for_user


class CurrentBusinessMiddleware:
    """Attach current membership/business to the request for tenant scoping."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request.membership = None
        request.business = None
        request.user_memberships = []

        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            memberships = list(memberships_for_user(user))
            request.user_memberships = memberships
            business_id = request.session.get("current_business_id")
            membership = get_active_membership(user, business_id)
            if membership is None and memberships:
                membership = memberships[0]
                request.session["current_business_id"] = str(membership.business_id)
            if membership is not None:
                request.membership = membership
                request.business = membership.business

        return self.get_response(request)
