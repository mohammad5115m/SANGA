"""Who may participate, who may sell, and who may still write.

Four questions that used to be answered by whatever check happened to be nearest,
and had drifted apart as a result. The matrix here exists because the drift was
only ever visible when two surfaces were compared side by side.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.businesses.directory import get_colleague
from apps.businesses.eligibility import (
    NotOperationalError,
    business_can_sell,
    business_can_use_app,
    business_is_network_eligible,
    require_operational,
)
from apps.businesses.models import Business, BusinessMembership
from apps.core.testing import make_business, make_item, make_product, make_user, owner_membership
from apps.inventory.policy import eligible_items, get_eligible_item, owned_items
from apps.inventory.services import InventoryError, confirm_item_stock, update_item
from apps.marketplace.selectors import marketplace_lots_for
from apps.pricing.services import ensure_default_tiers
from apps.trading.services import TradingError, create_purchase_request

#: Everything short of an approval. Refused states are separated because the
#: platform said no to those, rather than not yet having said yes.
UNAPPROVED_VERIFICATION_STATES = [
    Business.VerificationStatus.UNVERIFIED,
    Business.VerificationStatus.PENDING,
]
REFUSED_VERIFICATION_STATES = [
    Business.VerificationStatus.REJECTED,
    Business.VerificationStatus.SUSPENDED,
]


@pytest.fixture
def pair(db):
    ensure_default_tiers()
    seller = make_business(name="سنگ فروشنده", owner_phone="09361110001")
    buyer = make_business(name="سنگ خریدار", owner_phone="09361110002")
    item = make_item(
        seller,
        product=make_product(seller, commercial_name="تراورتن سنجش"),
        lot_code="EL-1",
        b2b="1000000",
        b2c="1500000",
    )
    return {
        "seller": seller,
        "buyer": buyer,
        "item": item,
        "seller_m": owner_membership(seller),
        "buyer_m": owner_membership(buyer),
    }


def _set(business: Business, **fields) -> Business:
    for name, value in fields.items():
        setattr(business, name, value)
    business.save(update_fields=list(fields))
    return business


def _expire(business: Business) -> Business:
    return _set(business, active_until=timezone.localdate() - timedelta(days=1))


# --- the four predicates are four different questions -------------------------


@pytest.mark.django_db
def test_provisioning_marks_a_business_verified(pair):
    """Provisioning *is* the approval: SANGA has no self-service signup, so a
    Business exists because an operator checked who it was.

    This is the half that used to be missing, and its absence was why the policy
    had to be a denylist — it could not require an approval that nothing ever
    recorded."""
    assert pair["seller"].verification_status == Business.VerificationStatus.VERIFIED
    assert business_is_network_eligible(pair["seller"]) is True
    assert business_can_sell(pair["seller"]) is True


@pytest.mark.django_db
@pytest.mark.parametrize("verification", UNAPPROVED_VERIFICATION_STATES)
def test_an_unapproved_business_is_not_on_the_network(pair, verification):
    """Only approved businesses participate. Not-yet-approved is not approved."""
    _set(pair["seller"], verification_status=verification)
    assert business_is_network_eligible(pair["seller"]) is False
    assert business_can_sell(pair["seller"]) is False
    # It can still operate on its own records; it is simply not shown to others.
    assert business_can_use_app(pair["seller"]) is True


@pytest.mark.django_db
@pytest.mark.parametrize("verification", REFUSED_VERIFICATION_STATES)
def test_a_refused_business_leaves_the_network(pair, verification):
    """REJECTED and SUSPENDED are decisions the platform actually took."""
    _set(pair["seller"], verification_status=verification)
    assert business_is_network_eligible(pair["seller"]) is False
    assert business_can_sell(pair["seller"]) is False
    assert business_can_use_app(pair["seller"]) is True


@pytest.mark.django_db
def test_the_policy_can_be_relaxed_for_a_demo_database(pair, settings):
    """The setting exists so a database seeded with unverified fixtures is not an
    empty site. Production defaults to requiring approval."""
    _set(pair["seller"], verification_status=Business.VerificationStatus.UNVERIFIED)
    assert business_is_network_eligible(pair["seller"]) is False

    settings.SANGA_REQUIRE_VERIFIED_FOR_NETWORK = False
    assert business_is_network_eligible(pair["seller"]) is True


@pytest.mark.django_db
def test_relaxing_the_policy_never_readmits_a_refused_business(pair, settings):
    """The two are different rules: one is "not yet approved", the other is
    "the platform said no"."""
    settings.SANGA_REQUIRE_VERIFIED_FOR_NETWORK = False
    _set(pair["seller"], verification_status=Business.VerificationStatus.REJECTED)
    assert business_is_network_eligible(pair["seller"]) is False


@pytest.mark.django_db
def test_a_browse_only_business_is_a_colleague_but_not_a_seller(pair):
    _set(pair["seller"], plan=Business.Plan.BROWSE)
    assert business_is_network_eligible(pair["seller"]) is True
    assert business_can_sell(pair["seller"]) is False


@pytest.mark.django_db
def test_an_expired_business_can_neither_sell_nor_write(pair):
    _expire(pair["seller"])
    assert business_can_use_app(pair["seller"]) is False
    assert business_is_network_eligible(pair["seller"]) is False
    assert business_can_sell(pair["seller"]) is False


@pytest.mark.django_db
def test_a_business_with_no_expiry_date_is_current(pair):
    """Null means "no expiry set", not "expired" — an admin who left the field
    blank must not lock the account out overnight."""
    _set(pair["seller"], active_until=None)
    assert business_can_use_app(pair["seller"]) is True


# --- published products follow the seller's eligibility -----------------------


@pytest.mark.django_db
def test_a_downgraded_seller_disappears_from_every_buyer_surface(client, pair):
    """AUD-005. The products stayed discoverable and the journey ended in an
    error when the buyer pressed «درخواست خرید»."""
    assert marketplace_lots_for(pair["buyer"]).count() == 1

    _set(pair["seller"], plan=Business.Plan.BROWSE)

    assert marketplace_lots_for(pair["buyer"]).count() == 0
    assert eligible_items(audience="public").count() == 0
    assert (
        get_eligible_item(audience="colleague", viewer_business=pair["buyer"], item_id=pair["item"].id) is None
    )
    assert client.get(f"/p/{pair['item'].public_token}/").status_code == 404


@pytest.mark.django_db
def test_an_expired_seller_disappears_from_every_buyer_surface(pair):
    _expire(pair["seller"])
    assert marketplace_lots_for(pair["buyer"]).count() == 0
    assert eligible_items(audience="public").count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize("verification", REFUSED_VERIFICATION_STATES)
def test_a_refused_seller_disappears_from_every_buyer_surface(pair, verification):
    """AUD-006. verification_status was modeled and then checked by nothing but
    a template badge."""
    _set(pair["seller"], verification_status=verification)
    assert marketplace_lots_for(pair["buyer"]).count() == 0
    assert eligible_items(audience="public").count() == 0


@pytest.mark.django_db
def test_the_seller_still_sees_its_own_withdrawn_products(pair):
    """Dropping off the buyer-facing surfaces is exactly when the seller most
    needs to find them."""
    _set(pair["seller"], plan=Business.Plan.BROWSE)
    assert owned_items(pair["seller"]).count() == 1


@pytest.mark.django_db
def test_a_buyer_cannot_request_from_an_ineligible_seller(pair):
    _set(pair["seller"], verification_status=Business.VerificationStatus.REJECTED)
    with pytest.raises(TradingError):
        create_purchase_request(
            buyer_business=pair["buyer"],
            membership=pair["buyer_m"],
            item=pair["item"],
            requested_qty_sqm=Decimal("10"),
            proposed_unit_price=Decimal("1000000"),
        )


# --- directory eligibility ----------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", Business.Status.SUSPENDED),
        ("verification_status", Business.VerificationStatus.REJECTED),
        ("verification_status", Business.VerificationStatus.SUSPENDED),
    ],
)
def test_an_ineligible_business_leaves_the_colleague_directory(pair, field, value):
    _set(pair["seller"], **{field: value})
    assert get_colleague(pair["buyer"], pair["seller"].id) is None


@pytest.mark.django_db
def test_a_browse_only_colleague_stays_in_the_directory(pair):
    """They can still be invoiced and owed money; they simply do not sell."""
    _set(pair["seller"], plan=Business.Plan.BROWSE)
    assert get_colleague(pair["buyer"], pair["seller"].id) is not None


@pytest.mark.django_db
def test_an_ineligible_viewer_sees_no_directory_and_no_marketplace(pair):
    _set(pair["buyer"], status=Business.Status.SUSPENDED)
    assert get_colleague(pair["buyer"], pair["seller"].id) is None
    assert marketplace_lots_for(pair["buyer"]).count() == 0


# --- the write gate -----------------------------------------------------------


@pytest.mark.django_db
def test_a_suspended_tenant_cannot_edit_its_own_products(pair):
    """AUD-004. The plan gate already blocked creating and publishing, because
    those consult entitlements. Editing consulted only the member's capability,
    so a suspended Business could keep working on everything it already had.
    """
    _set(pair["seller"], status=Business.Status.SUSPENDED)
    with pytest.raises(InventoryError):
        update_item(lot=pair["item"], membership=pair["seller_m"], fields={"grade": "درجه دو"})


@pytest.mark.django_db
def test_a_suspended_tenant_cannot_confirm_stock(pair):
    _set(pair["seller"], status=Business.Status.SUSPENDED)
    with pytest.raises(InventoryError):
        confirm_item_stock(lot=pair["item"], membership=pair["seller_m"], available_sqm=Decimal("5"))


@pytest.mark.django_db
def test_a_suspended_tenant_cannot_send_purchase_requests(pair):
    """Browse-only accounts buy without any seller entitlement, so the buying
    side had no operational gate at all before this."""
    _set(pair["buyer"], status=Business.Status.SUSPENDED)
    with pytest.raises(TradingError):
        create_purchase_request(
            buyer_business=pair["buyer"],
            membership=pair["buyer_m"],
            item=pair["item"],
            requested_qty_sqm=Decimal("10"),
            proposed_unit_price=Decimal("1000000"),
        )


@pytest.mark.django_db
def test_an_expired_tenant_is_told_to_renew_not_to_call_support(pair):
    _expire(pair["seller"])
    with pytest.raises(NotOperationalError) as exc:
        require_operational(pair["seller"])
    assert "اعتبار" in exc.value.message


@pytest.mark.django_db
def test_a_suspended_tenant_still_reads_its_own_records(client, pair):
    """The documented rule: the gate is on participation and on writing, not on
    seeing what already happened. See docs/permissions.md §8."""
    _set(pair["seller"], status=Business.Status.SUSPENDED)
    client.force_login(pair["seller_m"].user)
    session = client.session
    session["current_business_id"] = str(pair["seller"].id)
    session.save()

    assert client.get(reverse("inventory:lot_list")).status_code == 200
    assert client.get(reverse("invoicing:list")).status_code == 200
    assert client.get(reverse("accounting:index")).status_code == 200


# --- membership permissions ---------------------------------------------------


@pytest.mark.django_db
def test_omitting_permissions_materializes_the_role_defaults(pair):
    membership = BusinessMembership.objects.create(
        user=make_user("09361110010"),
        business=pair["seller"],
        role=BusinessMembership.Role.STAFF,
    )
    assert membership.permissions
    assert membership.has_capability("inventory.create")


@pytest.mark.django_db
def test_an_explicitly_empty_permission_list_stays_empty(pair):
    """AUD-008. ``if not self.permissions`` cannot tell "not initialized" from
    "deliberately empty", so stripping a member's access handed the role
    defaults straight back."""
    membership = BusinessMembership.objects.create(
        user=make_user("09361110011"),
        business=pair["seller"],
        role=BusinessMembership.Role.STAFF,
        permissions=[],
    )
    membership.refresh_from_db()
    assert membership.permissions == []
    assert membership.has_capability("inventory.create") is False


@pytest.mark.django_db
def test_stripping_an_existing_members_permissions_is_not_undone(pair):
    membership = BusinessMembership.objects.create(
        user=make_user("09361110012"),
        business=pair["seller"],
        role=BusinessMembership.Role.MANAGER,
    )
    assert membership.has_capability("sale.finalize")

    membership.permissions = []
    membership.save()
    membership.refresh_from_db()

    assert membership.permissions == []
    assert membership.has_capability("sale.finalize") is False


@pytest.mark.django_db
def test_a_partial_permission_list_is_taken_literally(pair):
    membership = BusinessMembership.objects.create(
        user=make_user("09361110013"),
        business=pair["seller"],
        role=BusinessMembership.Role.STAFF,
        permissions=["inventory.view"],
    )
    membership.refresh_from_db()
    assert membership.permissions == ["inventory.view"]
    assert membership.has_capability("inventory.view") is True
    assert membership.has_capability("inventory.create") is False
