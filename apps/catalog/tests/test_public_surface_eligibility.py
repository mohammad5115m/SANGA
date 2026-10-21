"""Every seller-specific public surface uses the same eligibility gate."""

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.businesses.models import Business
from apps.catalog.models import CustomCatalog, CustomCatalogItem
from apps.core.testing import make_business, make_item, make_product, owner_membership
from apps.pricing.services import ensure_default_tiers

INELIGIBLE = [
    pytest.param({"verification_status": Business.VerificationStatus.UNVERIFIED}, id="unverified"),
    pytest.param({"verification_status": Business.VerificationStatus.PENDING}, id="pending"),
    pytest.param({"verification_status": Business.VerificationStatus.REJECTED}, id="rejected"),
    pytest.param({"verification_status": Business.VerificationStatus.SUSPENDED}, id="verify-suspended"),
    pytest.param({"status": Business.Status.SUSPENDED}, id="suspended"),
    pytest.param({"plan": Business.Plan.BROWSE}, id="browse-only"),
]


@pytest.fixture
def shop(db):
    ensure_default_tiers()
    seller = make_business(name="سنگ ویترین", owner_phone="09211110001")
    item = make_item(
        seller,
        product=make_product(seller, commercial_name="تراورتن ویترین"),
        lot_code="PS-1",
        b2c="1500000",
    )
    catalog = CustomCatalog.objects.create(business=seller, title="کاتالوگ ویترین", is_active=True)
    CustomCatalogItem.objects.create(catalog=catalog, lot=item)
    return {"seller": seller, "item": item, "catalog": catalog, "membership": owner_membership(seller)}


def _break_eligibility(business, fields):
    for name, value in fields.items():
        setattr(business, name, value)
    business.save(update_fields=list(fields))


def _urls(shop):
    token = shop["seller"].storefront_token
    return {
        "storefront": reverse("catalog:storefront", kwargs={"storefront_token": token}),
        "lot_detail": reverse(
            "catalog:lot_detail", kwargs={"storefront_token": token, "lot_id": shop["item"].id}
        ),
        "share_token": reverse(
            "catalog:shared_item", kwargs={"public_token": shop["item"].public_token}
        ),
        "shared_catalog": reverse(
            "catalog:shared_catalog", kwargs={"share_token": shop["catalog"].share_token}
        ),
    }


@pytest.mark.django_db
@pytest.mark.parametrize("surface", ["storefront", "lot_detail", "share_token", "shared_catalog"])
def test_eligible_seller_is_reachable_on_every_surface(client, shop, surface):
    assert client.get(_urls(shop)[surface]).status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("fields", INELIGIBLE)
@pytest.mark.parametrize("surface", ["storefront", "lot_detail", "share_token", "shared_catalog"])
def test_ineligible_seller_gets_generic_refusal(client, shop, fields, surface):
    _break_eligibility(shop["seller"], fields)
    response = client.get(_urls(shop)[surface])
    assert response.status_code == 404
    body = response.content.decode()
    assert "سنگ ویترین" not in body
    assert "تراورتن ویترین" not in body


@pytest.mark.django_db
def test_invalid_storefront_token_is_indistinguishable(client, shop):
    withdrawn = client.get(
        reverse("catalog:storefront", kwargs={"storefront_token": "not-a-real-secure-token"})
    )
    assert withdrawn.status_code == 404


@pytest.mark.django_db
def test_slug_route_and_global_discovery_are_removed(client, shop):
    assert client.get(f"/s/{shop['seller'].slug}/").status_code == 404
    assert client.get("/search/").status_code == 404
    assert client.get(f"/store/{shop['seller'].storefront_token}/compare/").status_code == 404


@pytest.mark.django_db
def test_hidden_product_is_refused_without_closing_store(client, shop):
    shop["item"].is_visible = False
    shop["item"].save(update_fields=["is_visible"])
    urls = _urls(shop)
    assert client.get(urls["lot_detail"]).status_code == 404
    assert client.get(urls["share_token"]).status_code == 404
    assert client.get(urls["storefront"]).status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("fields", INELIGIBLE)
def test_ineligible_seller_can_still_read_own_history(client, shop, fields):
    from apps.invoicing.selectors import invoices_for
    from apps.trading.services import record_direct_sale

    trade = record_direct_sale(
        seller_business=shop["seller"],
        membership=shop["membership"],
        item=shop["item"],
        quantity_sqm=Decimal("10"),
        unit_price=Decimal("1000000"),
        customer_name="مشتری ویترین",
    )
    _break_eligibility(shop["seller"], fields)
    assert invoices_for(shop["seller"]).filter(trade=trade).exists()
