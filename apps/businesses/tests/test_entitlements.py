"""Plan entitlements and seat limits.

Two rules under test throughout: a plan gate must hold at the *service* layer,
not just in navigation, and it is a separate question from what the member is
allowed to do.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.businesses.entitlements import (
    CREATE_PRODUCTS,
    FINALIZE_SALES,
    MANAGE_CATALOGS,
    PUBLISH_PRODUCTS,
    EntitlementError,
    entitlements_for,
    has_entitlement,
    is_operational,
    require_entitlement,
    require_seat_available,
    seats_remaining,
    subscription_is_current,
)
from apps.businesses.models import Business, BusinessMembership
from apps.businesses.services import BusinessServiceError, invite_member
from apps.catalog.services import CatalogError, create_custom_catalog
from apps.core.testing import make_business, make_item, make_product, make_user, owner_membership
from apps.inventory.models import InventoryLot
from apps.inventory.services import (
    InventoryError,
    create_draft_item,
    publish_item,
    set_item_visibility,
    update_item,
)
from apps.pricing.services import ensure_default_tiers


@pytest.fixture
def seller(db):
    ensure_default_tiers()
    return make_business(name="سنگ فروشنده", owner_phone="09131110001")


@pytest.fixture
def browser(db):
    ensure_default_tiers()
    business = make_business(name="سنگ بیننده", owner_phone="09131110002")
    business.plan = Business.Plan.BROWSE
    business.save(update_fields=["plan"])
    return business


# --- plan shape ---------------------------------------------------------------


@pytest.mark.django_db
def test_seller_plan_covers_the_selling_actions(seller):
    granted = entitlements_for(seller)
    assert CREATE_PRODUCTS in granted
    assert PUBLISH_PRODUCTS in granted
    assert FINALIZE_SALES in granted
    assert MANAGE_CATALOGS in granted


@pytest.mark.django_db
def test_browse_plan_covers_none_of_them(browser):
    assert entitlements_for(browser) == frozenset()


@pytest.mark.django_db
def test_suspended_business_has_no_entitlements(seller):
    seller.status = Business.Status.SUSPENDED
    seller.save(update_fields=["status"])
    assert entitlements_for(seller) == frozenset()
    assert is_operational(seller) is False


# --- subscription expiry ------------------------------------------------------


@pytest.mark.django_db
def test_no_expiry_date_means_no_expiry(seller):
    """A field an admin forgot to fill in must not lock the account out."""
    assert seller.active_until is None
    assert subscription_is_current(seller) is True
    assert has_entitlement(seller, CREATE_PRODUCTS) is True


@pytest.mark.django_db
def test_expired_subscription_withdraws_everything(seller):
    seller.active_until = timezone.localdate() - timedelta(days=1)
    seller.save(update_fields=["active_until"])

    assert subscription_is_current(seller) is False
    assert entitlements_for(seller) == frozenset()
    with pytest.raises(EntitlementError):
        require_entitlement(seller, CREATE_PRODUCTS)


@pytest.mark.django_db
def test_subscription_is_current_on_its_last_day(seller):
    seller.active_until = timezone.localdate()
    seller.save(update_fields=["active_until"])
    assert subscription_is_current(seller) is True


# --- enforcement is in the service layer, not the navigation ------------------


@pytest.mark.django_db
def test_browse_only_business_cannot_create_a_product(browser):
    membership = owner_membership(browser)
    product = make_product(browser)

    with pytest.raises(InventoryError) as exc:
        create_draft_item(
            business=browser,
            membership=membership,
            product=product,
            available_sqm=Decimal("10"),
        )
    assert "پشتیبانی" in exc.value.message
    assert not InventoryLot.objects.filter(business=browser).exists()


@pytest.mark.django_db
def test_browse_only_business_cannot_publish_an_existing_product(browser):
    """Even an item created before a downgrade cannot be published."""
    item = make_item(browser, is_visible=False)
    membership = owner_membership(browser)

    with pytest.raises(InventoryError):
        set_item_visibility(lot=item, membership=membership, is_visible=True)
    with pytest.raises(InventoryError):
        publish_item(lot=item, membership=membership, is_visible=True)
    with pytest.raises(InventoryError):
        update_item(lot=item, membership=membership, fields={"is_visible": True})

    item.refresh_from_db()
    assert item.is_visible is False


@pytest.mark.django_db
def test_browse_only_business_may_still_unpublish(browser):
    """Withdrawing a product is never blocked by a plan."""
    item = make_item(browser, is_visible=True)
    set_item_visibility(lot=item, membership=owner_membership(browser), is_visible=False)
    item.refresh_from_db()
    assert item.is_visible is False


@pytest.mark.django_db
def test_browse_only_business_cannot_create_a_catalog(browser):
    with pytest.raises(CatalogError):
        create_custom_catalog(
            business=browser,
            membership=owner_membership(browser),
            title="کاتالوگ ممنوع",
        )


@pytest.mark.django_db
def test_seller_can_do_all_of_that(seller):
    membership = owner_membership(seller)
    item = create_draft_item(
        business=seller,
        membership=membership,
        product=make_product(seller),
        available_sqm=Decimal("10"),
    )
    publish_item(lot=item, membership=membership, is_visible=True)
    item.refresh_from_db()
    assert item.is_visible is True

    catalog = create_custom_catalog(business=seller, membership=membership, title="کاتالوگ مجاز")
    assert catalog.pk is not None


@pytest.mark.django_db
def test_expired_seller_is_blocked_the_same_way_a_browser_is(seller):
    seller.active_until = timezone.localdate() - timedelta(days=1)
    seller.save(update_fields=["active_until"])

    with pytest.raises(InventoryError) as exc:
        create_draft_item(
            business=seller,
            membership=owner_membership(seller),
            product=make_product(seller),
            available_sqm=Decimal("5"),
        )
    assert "اعتبار" in exc.value.message


# --- seats --------------------------------------------------------------------


@pytest.mark.django_db
def test_seat_limit_is_enforced_when_adding_a_member(seller):
    seller.seat_limit = 2
    seller.save(update_fields=["seat_limit"])
    owner = owner_membership(seller)

    assert seats_remaining(seller) == 1
    invite_member(business=seller, actor_membership=owner, user=make_user("09131119001"))
    assert seats_remaining(seller) == 0

    with pytest.raises(BusinessServiceError) as exc:
        invite_member(business=seller, actor_membership=owner, user=make_user("09131119002"))
    assert "سقف کاربران" in exc.value.message
    assert seller.memberships.filter(status=BusinessMembership.Status.ACTIVE).count() == 2


@pytest.mark.django_db
def test_reinviting_an_existing_active_member_consumes_no_seat(seller):
    seller.seat_limit = 2
    seller.save(update_fields=["seat_limit"])
    owner = owner_membership(seller)
    user = make_user("09131119003")

    first = invite_member(business=seller, actor_membership=owner, user=user)
    second = invite_member(business=seller, actor_membership=owner, user=user)
    assert first.pk == second.pk
    assert seats_remaining(seller) == 0


@pytest.mark.django_db
def test_lowering_the_seat_limit_does_not_evict_anyone(seller):
    """The limit bites on the next addition, not retroactively."""
    seller.seat_limit = 3
    seller.save(update_fields=["seat_limit"])
    owner = owner_membership(seller)
    invite_member(business=seller, actor_membership=owner, user=make_user("09131119004"))

    seller.seat_limit = 1
    seller.save(update_fields=["seat_limit"])

    assert seller.memberships.filter(status=BusinessMembership.Status.ACTIVE).count() == 2
    assert seats_remaining(seller) == 0
    with pytest.raises(EntitlementError):
        require_seat_available(seller)


@pytest.mark.django_db
def test_a_suspended_member_frees_their_seat(seller):
    seller.seat_limit = 2
    seller.save(update_fields=["seat_limit"])
    owner = owner_membership(seller)
    membership = invite_member(business=seller, actor_membership=owner, user=make_user("09131119005"))

    assert seats_remaining(seller) == 0
    membership.status = BusinessMembership.Status.SUSPENDED
    membership.save(update_fields=["status"])
    assert seats_remaining(seller) == 1
