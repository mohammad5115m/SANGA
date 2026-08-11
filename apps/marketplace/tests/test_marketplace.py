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
    }


@pytest.mark.django_db
def test_b2b_context_excludes_b2c(two_businesses):
    ctx = b2b_price_context(two_businesses["all_partners_lot"])
    assert ctx["amount"] == Decimal("1500000")
    assert "b2c" not in ctx
    assert "2500000" not in str(ctx)


@pytest.mark.django_db
def test_marketplace_visibility_matrix(two_businesses):
    buyer = two_businesses["buyer"]
    qs = marketplace_lots_for(buyer)
    ids = set(qs.values_list("lot_code", flat=True))
    assert "NET-1" in ids
    assert "SEL-1" not in ids  # needs approved relation
    assert "HID-1" not in ids

    request_partnership(
        partner_business=buyer,
        supplier_business=two_businesses["supplier"],
        membership=two_businesses["membership_b"],
    )
    relation = PartnerRelation.objects.get(
        partner_business=buyer,
        supplier_business=two_businesses["supplier"],
    )
    decide_partnership(relation=relation, membership=two_businesses["membership_a"], approve=True)

    qs = marketplace_lots_for(buyer)
    ids = set(qs.values_list("lot_code", flat=True))
    assert "SEL-1" in ids
    assert "HID-1" not in ids


@pytest.mark.django_db
def test_marketplace_page_shows_b2b_not_b2c(client, two_businesses):
    client.force_login(two_businesses["owner_b"])
    session = client.session
    session["current_business_id"] = str(two_businesses["buyer"].id)
    session.save()

    response = client.get(reverse("marketplace:home"))
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "1500000" in content.replace(",", "")
    assert "2500000" not in content.replace(",", "")
    assert "HID-1" not in content


@pytest.mark.django_db
def test_anonymous_cannot_open_marketplace(client):
    response = client.get(reverse("marketplace:home"))
    assert response.status_code in {302, 301}


@pytest.mark.django_db
def test_selected_lot_detail_requires_approval(client, two_businesses):
    client.force_login(two_businesses["owner_b"])
    session = client.session
    session["current_business_id"] = str(two_businesses["buyer"].id)
    session.save()

    selected = two_businesses["selected_lot"]
    assert get_marketplace_lot(two_businesses["buyer"], selected.id) is None
    response = client.get(reverse("marketplace:lot_detail", kwargs={"lot_id": selected.id}))
    assert response.status_code == 302
