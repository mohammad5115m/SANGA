"""Response headers that constrain what a page is allowed to do.

Django ships `SECURE_*` settings for most of the transport-level headers. The
ones here are the content-level ones it does not: a Content-Security-Policy, and
a Permissions-Policy for device APIs SANGA never uses.

The policy is deliberately narrow enough to be worth having. `script-src 'self'`
is the load-bearing clause and it only became possible once HTMX was self-hosted
and the last inline script moved into ``static/js/app.js`` — a policy
with `unsafe-inline` in it is close to no policy at all.
"""

from __future__ import annotations

from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse

#: Google Fonts is the one third-party origin left, and only for a stylesheet and
#: the font files it names. Stylesheets cannot execute, which is why this is a
#: different risk from a remote script. Self-hosting the font would remove it;
#: until then it is named explicitly rather than covered by a wildcard.
_FONT_CSS = "https://fonts.googleapis.com"
_FONT_FILES = "https://fonts.gstatic.com"

DEFAULT_CSP: dict[str, tuple[str, ...]] = {
    "default-src": ("'self'",),
    "script-src": ("'self'",),
    # Components use native hidden/details state. Google Fonts serves the only
    # external stylesheet; styles cannot execute code.
    "style-src": ("'self'", "'unsafe-inline'", _FONT_CSS),
    "font-src": ("'self'", _FONT_FILES, "data:"),
    # Product media may be served from object storage, so the image and media
    # sources are configurable; everything else is not.
    "img-src": ("'self'", "data:", "blob:"),
    "media-src": ("'self'", "blob:"),
    "connect-src": ("'self'",),
    "frame-ancestors": ("'none'",),
    "form-action": ("'self'",),
    "base-uri": ("'self'",),
    "object-src": ("'none'",),
}

#: Device APIs SANGA has no use for. Denying them means a compromised or
#: mistaken script cannot quietly ask for a microphone on a seller's phone.
PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"


def build_csp() -> str:
    directives = {name: list(values) for name, values in DEFAULT_CSP.items()}
    for name in ("img-src", "media-src", "connect-src"):
        extra = getattr(settings, "CSP_EXTRA_SOURCES", {}).get(name, ())
        directives[name].extend(extra)
    return "; ".join(f"{name} {' '.join(values)}" for name, values in directives.items())


class SecurityHeadersMiddleware:
    """Attach the content-level security headers to every response.

    Report-only is available through ``CSP_REPORT_ONLY`` so a deployment can
    watch for violations before enforcing. Introducing a CSP straight to
    enforcing on a live site is how a policy gets hurriedly reverted, and a
    reverted policy protects nobody.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response
        self.policy = build_csp()
        self.header = (
            "Content-Security-Policy-Report-Only"
            if getattr(settings, "CSP_REPORT_ONLY", False)
            else "Content-Security-Policy"
        )

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        response.setdefault(self.header, self.policy)
        response.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
        return response
