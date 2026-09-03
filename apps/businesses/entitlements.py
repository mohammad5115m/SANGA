"""What a *Business* is entitled to do, as opposed to what a member may do.

Two separate questions, both enforced server-side, and both have to pass:

    plan says the business may publish  AND  membership says this user may publish

Keeping plan checks here rather than scattering ``if business.plan == ...``
through views and templates is the whole point: a browse-only account that is
blocked only by hidden navigation is not blocked at all, and the difference is
invisible in review when the check lives in a template.
"""

from __future__ import annotations

from django.utils import timezone

from .models import Business

# --- entitlement codes --------------------------------------------------------
CREATE_PRODUCTS = "create_products"
PUBLISH_PRODUCTS = "publish_products"
RECEIVE_PURCHASE_REQUESTS = "receive_purchase_requests"
FINALIZE_SALES = "finalize_sales"
MANAGE_CATALOGS = "manage_catalogs"
ISSUE_INVOICES = "issue_invoices"
MANAGE_LEDGER = "manage_ledger"

# Available to every active Business, whatever its plan. A browse-only account
# still needs to find products, ask to buy them, and see what it was invoiced.
BROWSE_ENTITLEMENTS: frozenset[str] = frozenset()

SELLER_ENTITLEMENTS: frozenset[str] = frozenset(
    {
        CREATE_PRODUCTS,
        PUBLISH_PRODUCTS,
        RECEIVE_PURCHASE_REQUESTS,
        FINALIZE_SALES,
        MANAGE_CATALOGS,
        ISSUE_INVOICES,
        MANAGE_LEDGER,
    }
)

_PLAN_ENTITLEMENTS: dict[str, frozenset[str]] = {
    Business.Plan.BROWSE: BROWSE_ENTITLEMENTS,
    Business.Plan.SELLER: SELLER_ENTITLEMENTS,
}

#: Plans that grant any selling entitlement at all, derived from the map above
#: rather than restated. ``apps.businesses.eligibility`` needs this as a SQL
#: condition, and a second hand-written list of "the selling plans" is exactly
#: the kind of duplicate that drifts.
SELLING_PLANS: tuple[str, ...] = tuple(
    plan for plan, granted in _PLAN_ENTITLEMENTS.items() if SELLER_ENTITLEMENTS & granted
)

#: Persian explanation shown when an entitlement is missing. Generic wording
#: would leave the user with no idea whether to contact support or change a
#: setting.
_DENIAL_MESSAGES: dict[str, str] = {
    CREATE_PRODUCTS: "پلن فعلی شما امکان ثبت محصول ندارد. برای فروش در سنگا با پشتیبانی تماس بگیرید.",
    PUBLISH_PRODUCTS: "پلن فعلی شما امکان انتشار محصول ندارد. برای فروش در سنگا با پشتیبانی تماس بگیرید.",
    RECEIVE_PURCHASE_REQUESTS: "پلن فعلی شما امکان دریافت درخواست خرید ندارد.",
    FINALIZE_SALES: "پلن فعلی شما امکان ثبت فروش ندارد.",
    MANAGE_CATALOGS: "پلن فعلی شما امکان ساخت کاتالوگ ندارد.",
    ISSUE_INVOICES: "پلن فعلی شما امکان صدور فاکتور ندارد.",
    MANAGE_LEDGER: "پلن فعلی شما امکان ثبت سند مالی ندارد.",
}

EXPIRED_MESSAGE = "اعتبار اشتراک کسب‌وکار شما تمام شده است. برای تمدید با پشتیبانی تماس بگیرید."
SUSPENDED_MESSAGE = "کسب‌وکار شما موقتاً معلق است. برای پیگیری با پشتیبانی تماس بگیرید."


class EntitlementError(Exception):
    """The Business's plan does not cover this action."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def subscription_is_current(business: Business) -> bool:
    """A missing ``active_until`` means no expiry, not an expired one."""
    if business.active_until is None:
        return True
    return business.active_until >= timezone.localdate()


def is_operational(business: Business) -> bool:
    """Can this Business participate at all right now?"""
    return business.status == Business.Status.ACTIVE and subscription_is_current(business)


def entitlements_for(business: Business | None) -> frozenset[str]:
    if business is None or not is_operational(business):
        return frozenset()
    return _PLAN_ENTITLEMENTS.get(business.plan, BROWSE_ENTITLEMENTS)


def has_entitlement(business: Business | None, entitlement: str) -> bool:
    return entitlement in entitlements_for(business)


def require_entitlement(business: Business | None, entitlement: str) -> None:
    """Raise :class:`EntitlementError` unless the plan covers ``entitlement``.

    Called from services, not views, so the rule holds no matter which entry
    point reaches it.
    """
    if business is None:
        raise EntitlementError(SUSPENDED_MESSAGE)
    if business.status != Business.Status.ACTIVE:
        raise EntitlementError(SUSPENDED_MESSAGE)
    if not subscription_is_current(business):
        raise EntitlementError(EXPIRED_MESSAGE)
    if entitlement not in entitlements_for(business):
        raise EntitlementError(_DENIAL_MESSAGES.get(entitlement, "پلن فعلی شما این امکان را ندارد."))


# --- seats --------------------------------------------------------------------


def active_seat_count(business: Business) -> int:
    from .models import BusinessMembership

    return BusinessMembership.objects.filter(
        business=business,
        status=BusinessMembership.Status.ACTIVE,
    ).count()


def seats_remaining(business: Business) -> int:
    return max(business.seat_limit - active_seat_count(business), 0)


def require_seat_available(business: Business) -> None:
    """Raise unless the Business can activate one more member.

    Enforced when a membership is created or reactivated rather than on every
    login: a Business whose seat limit is lowered keeps its existing people
    working, and the limit bites the next time someone is added.
    """
    if seats_remaining(business) <= 0:
        raise EntitlementError(
            f"سقف کاربران این کسب‌وکار ({business.seat_limit} نفر) تکمیل است. "
            "برای افزودن کاربر بیشتر با پشتیبانی تماس بگیرید."
        )
