from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.businesses.models import BusinessMembership
from apps.businesses.services import add_warehouse, create_business_for_owner
from apps.inventory.models import InventoryLot, Product
from apps.marketplace.selectors import get_marketplace_lot, marketplace_lots_for
from apps.marketplace.services import b2b_price_context
from apps.partners.models import PartnerRelation
from apps.partners.services import decide_partnership, request_partnership
from apps.pricing.services import ensure_default_tiers, set_lot_prices

User = get_user_model()


@pytest.fixture
def two_businesses(db):
    ensure_default_tiers()
    owner_a = User.objects.create_user(phone="09126660001", full_name="تأمین")
    owner_b = User.objects.create_user(phone="09126660002", full_name="خریدار")
    supplier = create_business_for_owner(owner=owner_a, name="تأمین‌کننده آلفا", city="محلات")
    buyer = create_business_for_owner(owner=owner_b, name="خریدار بتا", city="تهران")
    wh = add_warehouse(business=supplier, name="انبار", is_default=True)
    membership_a = BusinessMembership.objects.get(user=owner_a, business=supplier)
    membership_b = BusinessMembership.objects.get(user=owner_b, business=buyer)
    product = Product.objects.create(business=supplier, commercial_name="مرمریت شبکه", stone_type="مرمریت")

    def make_lot(code, visibility, b2b="1000000", b2c="2000000"):
        lot = InventoryLot.objects.create(
            business=supplier,
            product=product,
            warehouse=wh,
            lot_code=code,
            status=InventoryLot.Status.AVAILABLE,
            visibility=visibility,
            available_sqm=Decimal("50"),
            original_sqm=Decimal("50"),
            inventory_confirmed_at=timezone.now(),
        )
        set_lot_prices(lot=lot, b2b_amount=Decimal(b2b), b2c_amount=Decimal(b2c))
        return lot

    return {
        "owner_a": owner_a,
        "owner_b": owner_b,
        "supplier": supplier,
        "buyer": buyer,
        "membership_a": membership_a,
        "membership_b": membership_b,
        "all_partners_lot": make_lot("NET-1", InventoryLot.Visibility.ALL_PARTNERS, "1500000", "2500000"),
        "selected_lot": make_lot("SEL-1", InventoryLot.Visibility.SELECTED_PARTNERS, "1600000", "2600000"),
        "private_lot": make_lot("HID-1", InventoryLot.Visibility.PRIVATE, "1700000", "2700000"),
        "public_lot": make_lot("PUB-1", InventoryLot.Visibility.PUBLIC, "1800000", "2800000"),
    }


def _request_partnership(ctx) -> PartnerRelation:
    request_partnership(
        partner_business=ctx["buyer"],
        supplier_business=ctx["supplier"],
        membership=ctx["membership_b"],
    )
    return PartnerRelation.objects.get(
        partner_business=ctx["buyer"],
        supplier_business=ctx["supplier"],
    )


def _decide_partnership(ctx, *, approve: bool) -> PartnerRelation:
    return decide_partnership(
        relation=_request_partnership(ctx),
        membership=ctx["membership_a"],
        approve=approve,
    )


def _login_as_buyer(client, ctx) -> None:
    client.force_login(ctx["owner_b"])
    session = client.session
    session["current_business_id"] = str(ctx["buyer"].id)
    session.save()


@pytest.mark.django_db
def test_b2b_context_excludes_b2c(two_businesses):
    ctx = b2b_price_context(two_businesses["all_partners_lot"])
    assert ctx["amount"] == Decimal("1500000")
    assert "b2c" not in ctx
    assert "2500000" not in str(ctx)


@pytest.mark.django_db
def test_marketplace_visibility_matrix(two_businesses):
    buyer = two_businesses["buyer"]
    codes = set(marketplace_lots_for(buyer).values_list("lot_code", flat=True))
    # Without an approved partnership only public lots are reachable.
    assert codes == {"PUB-1"}

    _decide_partnership(two_businesses, approve=True)

    codes = set(marketplace_lots_for(buyer).values_list("lot_code", flat=True))
    assert "NET-1" in codes
    assert "SEL-1" in codes
    assert "HID-1" not in codes


@pytest.mark.django_db
def test_non_partner_cannot_see_partner_only_lots(two_businesses):
    codes = set(marketplace_lots_for(two_businesses["buyer"]).values_list("lot_code", flat=True))
    assert "NET-1" not in codes
    assert "SEL-1" not in codes


@pytest.mark.django_db
def test_non_partner_cannot_fetch_partner_only_lot_by_uuid(client, two_businesses):
    _login_as_buyer(client, two_businesses)
    lot = two_businesses["all_partners_lot"]

    assert get_marketplace_lot(two_businesses["buyer"], lot.id) is None

    response = client.get(reverse("marketplace:lot_detail", kwargs={"lot_id": lot.id}), follow=True)
    assert response.redirect_chain
    content = response.content.decode("utf-8").replace(",", "")
    assert "NET-1" not in content
    assert "1500000" not in content


@pytest.mark.django_db
def test_approved_partner_sees_lot_with_b2b_price(two_businesses):
    _decide_partnership(two_businesses, approve=True)

    lot = get_marketplace_lot(two_businesses["buyer"], two_businesses["all_partners_lot"].id)
    assert lot is not None
    # The prefetch must stay B2B-only even for an approved partner.
    assert {price.tier.code for price in lot.prices.all()} == {"b2b"}
    assert b2b_price_context(lot)["amount"] == Decimal("1500000")


@pytest.mark.django_db
def test_pending_partnership_grants_no_access(two_businesses):
    relation = _request_partnership(two_businesses)
    assert relation.status == PartnerRelation.Status.REQUESTED

    codes = set(marketplace_lots_for(two_businesses["buyer"]).values_list("lot_code", flat=True))
    assert "NET-1" not in codes
    assert get_marketplace_lot(two_businesses["buyer"], two_businesses["all_partners_lot"].id) is None


@pytest.mark.django_db
def test_rejected_partnership_grants_no_access(two_businesses):
    relation = _decide_partnership(two_businesses, approve=False)
    assert relation.status == PartnerRelation.Status.REJECTED

    codes = set(marketplace_lots_for(two_businesses["buyer"]).values_list("lot_code", flat=True))
    assert "NET-1" not in codes
    assert get_marketplace_lot(two_businesses["buyer"], two_businesses["all_partners_lot"].id) is None


@pytest.mark.django_db
def test_private_lot_never_visible(two_businesses):
    private_lot = two_businesses["private_lot"]
    assert get_marketplace_lot(two_businesses["buyer"], private_lot.id) is None

    _decide_partnership(two_businesses, approve=True)
    assert get_marketplace_lot(two_businesses["buyer"], private_lot.id) is None
    # The owner still reaches it through its own inventory, not the marketplace.
    assert get_marketplace_lot(two_businesses["supplier"], private_lot.id) is None


@pytest.mark.django_db
def test_partnership_gate_costs_no_query_per_lot(two_businesses, django_assert_num_queries):
    _decide_partnership(two_businesses, approve=True)

    codes = marketplace_lots_for(two_businesses["buyer"]).values_list("lot_code", flat=True)
    with django_assert_num_queries(1):
        assert sorted(codes) == ["NET-1", "PUB-1", "SEL-1"]


@pytest.mark.django_db
def test_marketplace_excludes_viewers_own_lots(two_businesses):
    _decide_partnership(two_businesses, approve=True)

    codes = set(marketplace_lots_for(two_businesses["supplier"]).values_list("lot_code", flat=True))
    assert codes == set()


@pytest.mark.django_db
def test_marketplace_page_shows_b2b_not_b2c(client, two_businesses):
    _decide_partnership(two_businesses, approve=True)
    _login_as_buyer(client, two_businesses)

    response = client.get(reverse("marketplace:home"))
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "1500000" in content.replace(",", "")
    assert "2500000" not in content.replace(",", "")
    assert "HID-1" not in content


@pytest.mark.django_db
def test_marketplace_hides_supplier_visibility_choice(client, two_businesses):
    _decide_partnership(two_businesses, approve=True)
    _login_as_buyer(client, two_businesses)

    response = client.get(reverse("marketplace:home"))
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    # The cards are rendered...
    assert "مرمریت شبکه" in content
    # ...but whether the supplier published a lot publicly or to partners only is
    # an internal distribution decision and must not reach another business.
    assert InventoryLot.Visibility.PUBLIC.label not in content
    assert InventoryLot.Visibility.ALL_PARTNERS.label not in content
    assert InventoryLot.Visibility.SELECTED_PARTNERS.label not in content


@pytest.mark.django_db
def test_anonymous_cannot_open_marketplace(client):
    response = client.get(reverse("marketplace:home"))
    assert response.status_code in {302, 301}


@pytest.mark.django_db
def test_selected_lot_detail_requires_approval(client, two_businesses):
    _login_as_buyer(client, two_businesses)

    selected = two_businesses["selected_lot"]
    assert get_marketplace_lot(two_businesses["buyer"], selected.id) is None
    response = client.get(reverse("marketplace:lot_detail", kwargs={"lot_id": selected.id}))
    assert response.status_code == 302
