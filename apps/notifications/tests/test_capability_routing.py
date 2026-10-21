"""Notifications go to whoever can act on them, not to whoever holds a role.

SANGA authorizes by capability. Notifications did not: they went to OWNER and
MANAGER by role, which excluded exactly the wrong people. The default ``staff``
role holds ``leads.manage``, ``purchase.request`` and ``sale.finalize`` — a
salesperson can answer a customer inquiry and finalize a sale, and was the one
member of the team guaranteed never to be told there was one waiting.

The mirror case matters as much: a permission set edited to remove a capability
used to keep sending the notifications, so a manager who had been taken off sales
went on being told about them.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.businesses.models import BusinessMembership
from apps.businesses.permissions import LEADS_MANAGE, PURCHASE_REQUEST, SALE_FINALIZE
from apps.core.testing import make_business, make_item, make_product, make_user, owner_membership
from apps.inquiries.services import create_inquiry
from apps.notifications.models import Notification
from apps.notifications.services import members_who_can, notify_business
from apps.pricing.services import ensure_default_tiers
from apps.trading.services import create_purchase_request

pytestmark = pytest.mark.django_db


@pytest.fixture
def team():
    ensure_default_tiers()
    seller = make_business(name="سنگ تیم اعلان", owner_phone="09231110001")
    buyer = make_business(name="سنگ خریدار اعلان", owner_phone="09231110002")
    item = make_item(
        seller,
        product=make_product(seller, commercial_name="تراورتن اعلان"),
        lot_code="NT-1",
        b2b="1000000",
    )
    return {
        "seller": seller,
        "buyer": buyer,
        "item": item,
        "owner_m": owner_membership(seller),
        "buyer_m": owner_membership(buyer),
    }


def _member(business, *, phone: str, role: str, permissions=None) -> BusinessMembership:
    return BusinessMembership.objects.create(
        user=make_user(phone),
        business=business,
        role=role,
        permissions=permissions,
        status=BusinessMembership.Status.ACTIVE,
    )


def _recipients(business) -> set[str]:
    return set(
        Notification.objects.filter(business=business).values_list("user__phone", flat=True)
    )


# --- the resolver -------------------------------------------------------------


def test_the_default_salesperson_holds_the_capabilities_they_work_with(team):
    """If this changes, the routing below is aiming at the wrong people."""
    staff = _member(team["seller"], phone="09231111001", role=BusinessMembership.Role.STAFF)
    assert staff.has_capability(LEADS_MANAGE)
    assert staff.has_capability(PURCHASE_REQUEST)
    assert staff.has_capability(SALE_FINALIZE)


def test_a_viewer_holds_none_of_them(team):
    viewer = _member(team["seller"], phone="09231111002", role=BusinessMembership.Role.VIEWER)
    assert not viewer.has_capability(LEADS_MANAGE)
    assert not viewer.has_capability(PURCHASE_REQUEST)


def test_the_resolver_finds_a_custom_permission_set(team):
    """The case role-based routing could never handle: someone given exactly one
    capability and no role that implies it."""
    lead_handler = _member(
        team["seller"],
        phone="09231111003",
        role=BusinessMembership.Role.VIEWER,
        permissions=[LEADS_MANAGE],
    )

    found = members_who_can(team["seller"], LEADS_MANAGE)
    assert lead_handler in found


def test_the_resolver_respects_a_capability_that_was_taken_away(team):
    """A manager whose permissions were edited down must stop being told about
    work they can no longer do."""
    manager = _member(
        team["seller"],
        phone="09231111004",
        role=BusinessMembership.Role.MANAGER,
        permissions=[LEADS_MANAGE],
    )

    assert manager in members_who_can(team["seller"], LEADS_MANAGE)
    assert manager not in members_who_can(team["seller"], SALE_FINALIZE)


def test_an_inactive_member_is_never_a_recipient(team):
    suspended = _member(team["seller"], phone="09231111005", role=BusinessMembership.Role.STAFF)
    suspended.status = BusinessMembership.Status.SUSPENDED
    suspended.save(update_fields=["status"])

    assert suspended not in members_who_can(team["seller"], LEADS_MANAGE)


def test_an_owner_holds_every_capability_whatever_their_stored_permissions(team):
    """Owners bypass the list by design, and the resolver must honour that rather
    than reading the column."""
    owner = team["owner_m"]
    owner.permissions = []
    owner.save(update_fields=["permissions"])

    assert owner in members_who_can(team["seller"], SALE_FINALIZE)


def test_notifying_a_business_with_nobody_to_tell_delivers_nothing(team):
    """A legitimate state, not an error — but it must not raise, and it must not
    quietly fall back to notifying everybody."""
    owner = team["owner_m"]
    owner.status = BusinessMembership.Status.SUSPENDED
    owner.save(update_fields=["status"])

    delivered = notify_business(team["seller"], capability=LEADS_MANAGE, title="تست", body="")

    assert delivered == 0
    assert not Notification.objects.filter(business=team["seller"]).exists()


# --- the real notifications ---------------------------------------------------


def _inquire(team, callbacks) -> None:
    """Submit an inquiry and run the post-commit notification.

    ``_notify_seller`` is deliberately deferred to ``transaction.on_commit`` so a
    rolled-back submission does not announce itself. Nothing commits under the
    ordinary test transaction, so the callback has to be run explicitly.
    """
    with callbacks(execute=True):
        create_inquiry(
            business=team["seller"],
            name="آقای رضایی",
            phone="09121110000",
            items=[{"item": team["item"], "quantity": Decimal("30")}],
        )


def test_a_customer_inquiry_reaches_the_salesperson_who_answers_it(
    team, django_capture_on_commit_callbacks
):
    _member(team["seller"], phone="09231112001", role=BusinessMembership.Role.STAFF)

    _inquire(team, django_capture_on_commit_callbacks)

    told = _recipients(team["seller"])
    assert "09231112001" in told, "the staff member who calls the customer back was not told"
    assert "09231110001" in told, "the owner was not told either"


def test_a_customer_inquiry_does_not_reach_a_viewer(team, django_capture_on_commit_callbacks):
    """Not everybody: a notification list that includes people who cannot act is
    a notification list nobody reads."""
    _member(team["seller"], phone="09231112002", role=BusinessMembership.Role.VIEWER)

    _inquire(team, django_capture_on_commit_callbacks)

    assert "09231112002" not in _recipients(team["seller"])


def test_a_customer_inquiry_reaches_a_custom_lead_handler(
    team, django_capture_on_commit_callbacks
):
    _member(
        team["seller"],
        phone="09231112003",
        role=BusinessMembership.Role.VIEWER,
        permissions=[LEADS_MANAGE],
    )

    _inquire(team, django_capture_on_commit_callbacks)

    assert "09231112003" in _recipients(team["seller"])


def test_an_incoming_purchase_request_reaches_whoever_can_answer_it(team):
    _member(team["seller"], phone="09231113001", role=BusinessMembership.Role.STAFF)
    _member(team["seller"], phone="09231113002", role=BusinessMembership.Role.VIEWER)

    create_purchase_request(
        buyer_business=team["buyer"],
        membership=team["buyer_m"],
        item=team["item"],
        requested_qty_sqm=Decimal("10"),
        proposed_unit_price=Decimal("1000000"),
    )

    told = _recipients(team["seller"])
    assert "09231113001" in told
    assert "09231113002" not in told


def test_a_finalized_sale_reaches_the_buyers_side(team):
    from apps.trading.services import finalize_sale, respond_to_purchase_request

    _member(team["buyer"], phone="09231114001", role=BusinessMembership.Role.STAFF)
    request = create_purchase_request(
        buyer_business=team["buyer"],
        membership=team["buyer_m"],
        item=team["item"],
        requested_qty_sqm=Decimal("10"),
        proposed_unit_price=Decimal("1000000"),
    )
    respond_to_purchase_request(request=request, membership=team["owner_m"], accept=True)
    Notification.objects.all().delete()

    finalize_sale(request=request, membership=team["owner_m"])

    assert "09231114001" in _recipients(team["buyer"])
