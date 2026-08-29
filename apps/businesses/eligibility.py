"""Four questions about a Business that are *not* the same question.

Before this module they were answered ad hoc, and the answers had drifted:
buyer-facing queries checked `status=ACTIVE` and nothing else, write services
checked the plan, statements checked the colleague directory, and
`verification_status` was checked by a template badge and nothing more. The
result was a set of contradictions — a seller whose subscription had lapsed kept
their products in the marketplace right up until the buyer pressed «درخواست
خرید» and got an error, and a suspended debtor's statement disappeared while
their debt did not.

The four questions, deliberately kept apart:

``business_can_use_app``
    May this tenant *operate* — write anything at all? A suspended or expired
    Business may still read its own history (that is a documented product rule;
    see docs/permissions.md §8), but it may not create, publish, price or sell.

``business_is_network_eligible``
    Should this Business appear to other people — in the colleague directory,
    the marketplace, public search, a storefront or a share link?

``business_can_sell``
    May this Business be on the selling side of a transaction? Network
    eligibility plus the seller plan.

``business_has_history_with``
    Did these two ever transact? This one must **never** be folded into the
    others: it is what keeps invoices, statements and debts reachable after the
    network relationship ends. It lives in ``apps.accounting.selectors`` next to
    the queries that need it, and is named here only to say why it is absent.

Collapsing any two of these into one helper is how the contradictions came back
last time.
"""

from __future__ import annotations

from django.conf import settings
from django.db.models import Q, QuerySet
from django.utils import timezone

from .entitlements import (
    EXPIRED_MESSAGE,
    SELLER_ENTITLEMENTS,
    SELLING_PLANS,
    SUSPENDED_MESSAGE,
    entitlements_for,
    is_operational,
)
from .models import Business

#: Verification states that remove a Business from the shared network.
#:
#: Explicit platform refusals. Kept as a named set because they are refusals
#: rather than an absence of approval, and the messages a human sees differ.
UNTRUSTED_VERIFICATION_STATES: frozenset[str] = frozenset(
    {
        Business.VerificationStatus.REJECTED,
        Business.VerificationStatus.SUSPENDED,
    }
)


def _requires_verification() -> bool:
    """Whether only VERIFIED Businesses join the shared network. On by default.

    This was a denylist — everything except REJECTED and SUSPENDED participated —
    for a reason that was true at the time: ``verification_status`` defaults to
    ``unverified``, nothing in provisioning set it, and flipping to an allowlist
    would have emptied every directory on the day it shipped.

    That reasoning fixed the wrong half. SANGA has no public signup: a Business
    exists because a platform admin provisioned it, so "approved" is a decision
    somebody has already made and the field should record it. The fix is to make
    provisioning set the field and to backfill the businesses that predate it
    (``businesses.0006``), not to keep the policy loose because the data was
    empty.

    Still a setting, because a development or demo environment seeded with
    unverified fixtures should not be an empty site. Production defaults to on.
    """
    return bool(getattr(settings, "SANGA_REQUIRE_VERIFIED_FOR_NETWORK", True))


def business_can_use_app(business: Business | None) -> bool:
    """May this tenant write anything?

    False for a suspended Business and for one whose subscription has lapsed.
    Reading its own records stays allowed on purpose — the gate is on
    participation and on changing things, not on seeing what already happened.
    """
    if business is None:
        return False
    return is_operational(business)


def business_is_network_eligible(business: Business | None) -> bool:
    """Should this Business be visible to anyone other than itself?

    Active, subscription current, and — in production — verified by the platform.
    """
    if business is None:
        return False
    if not is_operational(business):
        return False
    if business.verification_status in UNTRUSTED_VERIFICATION_STATES:
        return False
    if _requires_verification() and business.verification_status != Business.VerificationStatus.VERIFIED:
        return False
    return True


def business_can_sell(business: Business | None) -> bool:
    """May this Business be on the selling side of a transaction?

    Read eligibility and write entitlement used to disagree: a seller could
    downgrade to browse-only or let their subscription lapse and their published
    products stayed discoverable, right up until ``create_purchase_request``
    re-checked the plan and refused. The buyer's journey ended in an error page
    for a product the platform had gone on advertising.
    """
    if not business_is_network_eligible(business):
        return False
    return bool(SELLER_ENTITLEMENTS & entitlements_for(business))


# --- the same predicates, as SQL ---------------------------------------------
#
# Kept in this module beside their Python twins so the two cannot drift. Buyer
# facing queries join against these rather than materialising a list of eligible
# tenant primary keys in Python, which would grow with the platform and cost a
# query on every discovery page.


def _subscription_current_q(field: str) -> Q:
    """Null ``active_until`` means "no expiry set", not "expired"."""
    return Q(**{f"{field}active_until__isnull": True}) | Q(**{f"{field}active_until__gte": timezone.localdate()})


def network_eligible_q(prefix: str = "") -> Q:
    """The SQL half of :func:`business_is_network_eligible`."""
    field = f"{prefix}__" if prefix else ""
    condition = (
        Q(**{f"{field}status": Business.Status.ACTIVE})
        & ~Q(**{f"{field}verification_status__in": tuple(UNTRUSTED_VERIFICATION_STATES)})
        & _subscription_current_q(field)
    )
    if _requires_verification():
        condition &= Q(**{f"{field}verification_status": Business.VerificationStatus.VERIFIED})
    return condition


def can_sell_q(prefix: str = "") -> Q:
    """The SQL half of :func:`business_can_sell`."""
    field = f"{prefix}__" if prefix else ""
    return network_eligible_q(prefix) & Q(**{f"{field}plan__in": SELLING_PLANS})


def public_business_by_storefront_token_or_none(token: str) -> Business | None:
    """Resolve the unguessable customer-facing storefront capability."""
    if not token:
        return None
    return Business.objects.filter(storefront_token=token).filter(can_sell_q()).first()


def network_eligible_businesses() -> QuerySet[Business]:
    """Every Business that may appear to others, whatever their plan.

    Browse-only accounts belong here: they are real colleagues who can be
    invoiced and owed money, they simply have nothing to sell.
    """
    return Business.objects.filter(network_eligible_q())


class NotOperationalError(Exception):
    """The tenant may not write right now."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def require_operational(business: Business | None) -> None:
    """Raise unless this tenant may write.

    Called from services rather than views, so a suspended Business is stopped
    however it reaches the write — including through a form that was already open
    when the suspension landed.

    The message names the actual cause: "suspended" and "expired" need different
    actions from the user, and a generic refusal sends them to support for
    something they could renew themselves.
    """
    if business is None or business.status != Business.Status.ACTIVE:
        raise NotOperationalError(SUSPENDED_MESSAGE)
    if not business_can_use_app(business):
        raise NotOperationalError(EXPIRED_MESSAGE)
