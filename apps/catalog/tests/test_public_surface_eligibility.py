"""Every public surface answers the same question the same way.

They did not. The storefront and the product page resolved a seller on
``status=ACTIVE`` alone, while the products on those pages went through the full
seller gate. An unverified, expired or browse-only seller therefore got a real
page carrying their name and city with nothing on it — which is worse than a 404
in both directions. The visitor is told a shop exists and appears to have sold
out; the seller is publicly listed as having nothing.

A shared catalog link had the same shape: gated on the catalog's own switches
only, so the link stayed live and empty after the seller left the network.

The rule everywhere: a seller who cannot sell is indistinguishable from a seller
who does not exist. Telling a stranger which of the two it is says something
about a business that has not agreed to be discussed.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.businesses.models import Business
from apps.catalog.models import CustomCatalog, CustomCatalogItem
from apps.core.testing import make_business, make_item, make_product, owner_membership
from apps.pricing.services import ensure_default_tiers

#: Every way a seller can be on the platform but not on the network.
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
    catalog = CustomCatalog.objects.create(
        business=seller,
        title="کاتالوگ ویترین",
        mode=CustomCatalog.Mode.MANUAL,
        is_active=True,
    )
    CustomCatalogItem.objects.create(catalog=catalog, lot=item)
    return {"seller": seller, "item": item, "catalog": catalog, "membership": owner_membership(seller)}


def _break_eligibility(business: Business, fields: dict) -> Business:
    for name, value in fields.items():
        setattr(business, name, value)
    business.save(update_fields=list(fields))
    return business


def _urls(shop) -> dict[str, str]:
    return {
        "storefront": reverse("catalog:storefront", kwargs={"business_slug": shop["seller"].slug}),
        "lot_detail": reverse(
            "catalog:lot_detail",
            kwargs={"business_slug": shop["seller"].slug, "lot_id": shop["item"].id},
        ),
        "compare": reverse("catalog:compare", kwargs={"business_slug": shop["seller"].slug}),
        "share_token": f"/p/{shop['item'].public_token}/",
        "shared_catalog": f"/c/{shop['catalog'].share_token}/",
    }


# --- an eligible seller is reachable everywhere -------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("surface", ["storefront", "lot_detail", "compare", "share_token", "shared_catalog"])
def test_an_eligible_seller_is_reachable_on_every_surface(client, shop, surface):
    assert client.get(_urls(shop)[surface]).status_code == 200


@pytest.mark.django_db
def test_an_eligible_seller_appears_in_public_search(client, shop):
    body = client.get(reverse("catalog:public_search")).content.decode()
    assert "تراورتن ویترین" in body


# --- an ineligible seller is reachable nowhere --------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("fields", INELIGIBLE)
@pytest.mark.parametrize("surface", ["storefront", "lot_detail", "compare", "share_token", "shared_catalog"])
def test_an_ineligible_seller_gets_a_generic_refusal(client, shop, fields, surface):
    _break_eligibility(shop["seller"], fields)
    assert client.get(_urls(shop)[surface]).status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize("fields", INELIGIBLE)
def test_an_ineligible_seller_disappears_from_public_search(client, shop, fields):
    _break_eligibility(shop["seller"], fields)
    body = client.get(reverse("catalog:public_search")).content.decode()
    assert "تراورتن ویترین" not in body


@pytest.mark.django_db
@pytest.mark.parametrize("fields", INELIGIBLE)
def test_the_refusal_never_names_the_business(client, shop, fields):
    """A 404 that renders the shop's name and city is not a refusal, it is the
    page with the products removed."""
    _break_eligibility(shop["seller"], fields)

    for url in _urls(shop).values():
        body = client.get(url).content.decode()
        assert "سنگ ویترین" not in body, f"{url} still names the business"
        assert "تراورتن ویترین" not in body, f"{url} still names the product"


@pytest.mark.django_db
@pytest.mark.parametrize("fields", INELIGIBLE)
def test_nothing_reveals_whether_the_seller_exists_at_all(client, shop, fields):
    """The refusal for a withdrawn seller must be the same as for a slug that
    was never registered."""
    _break_eligibility(shop["seller"], fields)
    urls = _urls(shop)

    withdrawn = client.get(urls["storefront"])
    never_existed = client.get(reverse("catalog:storefront", kwargs={"business_slug": "no-such-shop"}))
    assert withdrawn.status_code == never_existed.status_code == 404


# --- a hidden product is not distinguishable from a missing one ---------------


@pytest.mark.django_db
def test_a_hidden_product_of_an_eligible_seller_is_still_refused(client, shop):
    shop["item"].is_visible = False
    shop["item"].save(update_fields=["is_visible"])
    urls = _urls(shop)

    assert client.get(urls["lot_detail"]).status_code == 404
    assert client.get(urls["share_token"]).status_code == 404
    # …while the shop itself remains open, because the seller is fine.
    assert client.get(urls["storefront"]).status_code == 200


# --- history is deliberately not subject to any of this -----------------------


@pytest.mark.django_db
@pytest.mark.parametrize("fields", INELIGIBLE)
def test_an_ineligible_seller_can_still_read_their_own_records(client, shop, fields):
    """The gate is on being shown to other people, not on the business seeing
    what already happened to it."""
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
