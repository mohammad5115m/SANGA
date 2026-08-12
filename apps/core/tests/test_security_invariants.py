"""The invariants that must never break, gathered in one place.

Most are also covered where the feature lives. They are repeated here on purpose:
these are the rules that would cause real harm if a future change quietly
relaxed one, and a single failing file named "security invariants" is a clearer
signal than one failure buried in a feature suite.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.businesses.models import Business
from apps.core.testing import make_business, make_item, make_product, owner_membership
from apps.inventory.models import InventoryLot
from apps.inventory.policy import eligible_items
from apps.pricing.services import ensure_default_tiers, resolve_visible_prices

User = get_user_model()

B2B_PRICE = "1111111"
B2C_PRICE = "2222222"


@pytest.fixture
def world(db):
    from apps.catalog.services import create_custom_catalog

    ensure_default_tiers()
    seller = make_business(name="سنگ فروشنده", owner_phone="09261110001")
    colleague = make_business(name="سنگ همکار", owner_phone="09261110002")
    item = make_item(
        seller,
        product=make_product(seller, commercial_name="تراورتن آزمون"),
        lot_code="SEC-1",
        b2b=B2B_PRICE,
        b2c=B2C_PRICE,
    )
    # Curated while the product is still eligible, so the withdrawal tests below
    # exercise the read-time gate rather than the curation-time one.
    catalog = create_custom_catalog(
        business=seller,
        membership=owner_membership(seller),
        title="کاتالوگ آزمون",
        lot_ids=[item.id],
    )
    return {"seller": seller, "colleague": colleague, "item": item, "catalog": catalog}


def _public_bodies(client, world) -> dict[str, str]:
    """Every anonymous surface that renders a product."""
    catalog = world["catalog"]
    urls = {
        "search": reverse("catalog:public_search"),
        "storefront": reverse("catalog:storefront", kwargs={"business_slug": world["seller"].slug}),
        "detail": reverse(
            "catalog:lot_detail",
            kwargs={"business_slug": world["seller"].slug, "lot_id": world["item"].id},
        ),
        "share": f"/p/{world['item'].public_token}/",
        "catalog": reverse("catalog:shared_catalog", kwargs={"share_token": catalog.share_token}),
    }
    return {name: client.get(url).content.decode().replace(",", "") for name, url in urls.items()}


# --- Rule 1 & 2: only Platform Admin creates accounts ----------------------------


@pytest.mark.django_db
def test_authentication_never_creates_a_user(rf, settings):
    from apps.accounts.models import OTPChallenge
    from apps.accounts.services import (
        OTPValidationError,
        _hash_code,
        request_login_otp,
        verify_login_otp,
    )

    settings.DEBUG = True
    settings.SMS_PROVIDER = "console"

    request_login_otp("09260000001")
    challenge = OTPChallenge.objects.get(phone="09260000001")
    challenge.code_hash = _hash_code("123456")
    challenge.save(update_fields=["code_hash"])

    with pytest.raises(OTPValidationError):
        verify_login_otp("09260000001", "123456", request=rf.post("/auth/verify/"))
    assert not User.objects.filter(phone="09260000001").exists()


@pytest.mark.django_db
def test_there_is_no_route_that_creates_a_business(client):
    from django.urls import NoReverseMatch

    with pytest.raises(NoReverseMatch):
        reverse("businesses:onboarding_start")

    user = User.objects.create_user(phone="09260000002")
    client.force_login(user)
    assert client.get(reverse("businesses:dashboard")).status_code == 302
    assert not Business.objects.filter(memberships__user=user).exists()


@pytest.mark.django_db
def test_a_public_inquiry_creates_no_platform_user(world):
    from apps.inquiries.services import create_inquiry

    before = User.objects.count()
    create_inquiry(
        business=world["seller"],
        name="مشتری عمومی",
        phone="09260000003",
        items=[{"item": world["item"], "quantity": Decimal("10")}],
    )
    assert User.objects.count() == before


# --- Rule 6: B2B prices never leak publicly ---------------------------------------


@pytest.mark.django_db
def test_no_public_surface_ever_contains_the_b2b_price(client, world):
    for name, body in _public_bodies(client, world).items():
        assert B2B_PRICE not in body, f"B2B price leaked on {name}"


@pytest.mark.django_db
def test_a_logged_in_colleague_opening_a_share_link_sees_no_b2b_price(client, world):
    """A share URL is B2C-safe by construction, whoever opens it."""
    client.force_login(world["colleague"].memberships.get(role="owner").user)
    body = client.get(f"/p/{world['item'].public_token}/").content.decode().replace(",", "")
    assert B2B_PRICE not in body
    assert B2C_PRICE in body


@pytest.mark.django_db
def test_the_public_query_never_even_loads_a_b2b_row(world):
    """Defence in depth: what is not in memory cannot be rendered."""
    item = eligible_items(audience="public").first()
    assert {price.tier.code for price in item.prices.all()} == {"b2c"}


@pytest.mark.django_db
def test_price_resolution_omits_disallowed_tiers_entirely(world):
    visible = resolve_visible_prices(world["item"], "b2c_public")
    assert set(visible) == {"b2c"}
    assert B2B_PRICE not in str(visible)


@pytest.mark.django_db
def test_a_b2b_special_sale_is_not_public(client, world):
    from datetime import timedelta

    from apps.pricing.services import set_lot_price

    set_lot_price(
        lot=world["item"],
        tier_code="b2b",
        amount=Decimal(B2B_PRICE),
        special_amount=Decimal("999999"),
        special_until=timezone.now() + timedelta(days=1),
    )
    for name, body in _public_bodies(client, world).items():
        assert "999999" not in body, f"B2B special price leaked on {name}"


# --- Rules 22, 23, 30: lifecycle changes reach every surface ----------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda i: setattr(i, "is_visible", False), id="hidden"),
        pytest.param(
            lambda i: setattr(i, "availability_status", InventoryLot.Availability.UNAVAILABLE),
            id="unavailable",
        ),
        pytest.param(lambda i: setattr(i, "deleted_at", timezone.now()), id="deleted"),
    ],
)
def test_a_withdrawn_product_leaves_every_public_surface_at_once(client, world, mutate):
    mutate(world["item"])
    world["item"].save()

    for name, body in _public_bodies(client, world).items():
        assert "تراورتن آزمون" not in body, f"withdrawn product still visible on {name}"


@pytest.mark.django_db
def test_a_withdrawn_product_also_leaves_the_colleague_marketplace(world):
    from apps.marketplace.selectors import marketplace_lots_for

    assert world["item"] in marketplace_lots_for(world["colleague"])
    world["item"].availability_status = InventoryLot.Availability.UNAVAILABLE
    world["item"].save()
    assert world["item"] not in marketplace_lots_for(world["colleague"])


# --- Rule 7 & 8: expiry degrades the display, it does not hide the product --------


@pytest.mark.django_db
def test_expired_stock_keeps_the_product_discoverable(client, world):
    from apps.core.testing import expire_stock

    expire_stock(world["item"])
    bodies = _public_bodies(client, world)
    assert "تراورتن آزمون" in bodies["search"]
    assert "استعلام موجودی" in bodies["detail"]


@pytest.mark.django_db
def test_an_expired_price_hides_the_number_but_not_the_product(client, world):
    from apps.core.testing import expire_price

    expire_price(world["item"], "b2c")
    bodies = _public_bodies(client, world)
    assert "تراورتن آزمون" in bodies["search"]
    assert B2C_PRICE not in bodies["detail"]
    assert "استعلام قیمت" in bodies["detail"]


# --- tenant isolation ---------------------------------------------------------------


@pytest.mark.django_db
def test_a_business_cannot_reach_another_businesss_records(client, world):
    from apps.inventory.selectors import get_business_lot

    intruder = make_business(name="سنگ غریبه", owner_phone="09261110009")
    client.force_login(intruder.memberships.get(role="owner").user)
    session = client.session
    session["current_business_id"] = str(intruder.id)
    session.save()

    assert get_business_lot(intruder, world["item"].id) is None

    # The other business's product is not reachable at all.
    response = client.get(reverse("inventory:lot_detail", kwargs={"lot_id": world["item"].id}))
    assert response.status_code in (302, 404)

    # The statement page *is* reachable — every active Business is a colleague —
    # but it shows the intruder's own books, which are empty. What must never
    # appear is the seller's balance with anybody else.
    statement = client.get(reverse("accounting:statement", kwargs={"business_id": world["seller"].id}))
    assert statement.status_code == 200
    assert statement.context["entries"].count() == 0
    assert statement.context["balance"]["amount"] == Decimal("0.00")


@pytest.mark.django_db
def test_a_suspended_business_is_invisible_and_blind(world):
    from apps.businesses.directory import colleague_businesses
    from apps.marketplace.selectors import marketplace_lots_for

    world["seller"].status = Business.Status.SUSPENDED
    world["seller"].save(update_fields=["status"])

    assert list(marketplace_lots_for(world["colleague"])) == []
    assert world["seller"] not in colleague_businesses(world["colleague"])
    assert list(marketplace_lots_for(world["seller"])) == []


# --- Rule 14: a sale posts to the books exactly once ----------------------------------


@pytest.mark.django_db
def test_a_finalized_sale_moves_the_ledger_exactly_once(world):
    from apps.accounting.models import LedgerEntry
    from apps.accounting.selectors import current_balance
    from apps.trading.services import (
        TradingError,
        create_purchase_request,
        finalize_sale,
        respond_to_purchase_request,
    )

    request_ = create_purchase_request(
        buyer_business=world["colleague"],
        membership=owner_membership(world["colleague"]),
        item=world["item"],
        requested_qty_sqm=Decimal("10"),
        proposed_unit_price=Decimal("1000000"),
    )
    seller_m = owner_membership(world["seller"])
    respond_to_purchase_request(request=request_, membership=seller_m, accept=True)

    assert not LedgerEntry.objects.exists(), "accepting must not post"

    finalize_sale(request=request_, membership=seller_m)
    with pytest.raises(TradingError):
        finalize_sale(request=request_, membership=seller_m)

    assert LedgerEntry.objects.filter(entry_type=LedgerEntry.Type.SALE).count() == 1
    assert current_balance(world["seller"], world["colleague"]) == Decimal("10000000.00")


# --- Rule 17: invoices are immutable history --------------------------------------------


@pytest.mark.django_db
def test_an_invoice_does_not_change_when_the_product_does(world):
    from apps.invoicing.services import create_manual_invoice

    invoice = create_manual_invoice(
        business=world["seller"],
        membership=owner_membership(world["seller"]),
        lines=[{"product_name": "تراورتن آزمون", "quantity": Decimal("10"),
                "unit_price": Decimal("1000000"), "item": world["item"]}],
        buyer_business=world["colleague"],
    )
    product = world["item"].product
    product.commercial_name = "نام تازه"
    product.save(update_fields=["commercial_name"])

    line = invoice.items.get()
    line.refresh_from_db()
    assert line.product_name == "تراورتن آزمون"
    assert line.unit_price == Decimal("1000000.00")
