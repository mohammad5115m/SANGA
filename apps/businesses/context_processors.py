from __future__ import annotations

from django.http import HttpRequest

from .eligibility import business_can_use_app
from .entitlements import (
    CREATE_PRODUCTS,
    FINALIZE_SALES,
    ISSUE_INVOICES,
    MANAGE_CATALOGS,
    entitlements_for,
    subscription_is_current,
)
from .models import Business
from .permissions import (
    ALL_CAPABILITIES,
    CATALOG_MANAGE,
    INVENTORY_CREATE,
    INVOICE_MANAGE,
    SALE_FINALIZE,
)

#: Navigation entries that need a member capability *and* a plan entitlement.
#:
#: Templates used to check capabilities alone, so a browse-only or expired
#: Business still saw «افزودن محصول» and walked into a service error. Composing
#: the two here keeps the presentation rule in one place instead of repeating
#: ``{% if x in capabilities and y in entitlements %}`` at every call site.
_UI_ACTIONS: dict[str, tuple[str, str]] = {
    "can_add_products": (INVENTORY_CREATE, CREATE_PRODUCTS),
    "can_manage_catalogs": (CATALOG_MANAGE, MANAGE_CATALOGS),
    "can_finalize_sales": (SALE_FINALIZE, FINALIZE_SALES),
    "can_issue_invoices": (INVOICE_MANAGE, ISSUE_INVOICES),
}


def business_context(request: HttpRequest) -> dict:
    membership = getattr(request, "membership", None)
    business = getattr(request, "business", None)
    capabilities = _capability_codes(membership)
    entitlements = entitlements_for(business)

    context = {
        "current_business": business,
        "current_membership": membership,
        "user_memberships": getattr(request, "user_memberships", []),
        # Codes this member actually holds, so navigation can hide links that
        # would only end in «دسترسی ندارید». Derived from has_capability, so
        # owner bypass and suspended memberships behave identically to the
        # server-side checks. Never a substitute for them.
        "capabilities": capabilities,
        # What the Business's plan covers. Same caveat: this shapes navigation,
        # it does not enforce anything. The enforcement lives in the services,
        # via businesses.entitlements.require_entitlement.
        "entitlements": entitlements,
        # Whether this tenant may write at all. False for a suspended or expired
        # Business, which can still read everything it already has.
        "business_can_write": business_can_use_app(business),
        "business_block_reason": _block_reason(business),
        "notification_badge_count": _notification_badge_count(request, membership, business),
    }
    for name, (capability, entitlement) in _UI_ACTIONS.items():
        context[name] = capability in capabilities and entitlement in entitlements
    return context


def _notification_badge_count(request: HttpRequest, membership, business: Business | None) -> int:
    if not request.user.is_authenticated or business is None or membership is None:
        return 0

    from apps.businesses.permissions import LEADS_MANAGE

    if membership.has_capability(LEADS_MANAGE):
        from apps.inquiries.crm import CRMRepository

        # Session/demo follow-up reminders can be counted without a database
        # query. Persisted notification counts stay on the notification page;
        # adding a COUNT to every shell render would regress every list budget.
        return CRMRepository(request).unread_reminder_count()
    return 0


def _capability_codes(membership) -> frozenset[str]:
    if membership is None:
        return frozenset()
    return frozenset(code for code in ALL_CAPABILITIES if membership.has_capability(code))


def _block_reason(business: Business | None) -> str:
    """Why this tenant cannot write, in one word the template can switch on."""
    if business is None:
        return ""
    if business.status != Business.Status.ACTIVE:
        return "suspended"
    if not subscription_is_current(business):
        return "expired"
    return ""
