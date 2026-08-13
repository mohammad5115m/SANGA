from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import CustomCatalog
from apps.catalog.services import CatalogError, b2c_price_context, create_custom_catalog, set_catalog_lots
from apps.core.testing import (
    expire_price,
    expire_stock,
    make_business,
    make_item,
    make_product,
    owner_membership,
)
from apps.inquiries.models import Inquiry
from apps.inventory.models import InventoryLot
from apps.pricing.services import ensure_default_tiers

B2B = "1111111"
B2C = "2222222"
PRIVATE_B2B = "3333333"
PRIVATE_B2C = "4444444"


@pytest.fixture
def seller_setup(db):
    ensure_default_tiers()
    business = make_business(name="سنگسرا دمو", owner_phone="09125550001", city="اصفهان")
    product = make_product(
        business,
        commercial_name="تراورتن کرم دمو",
        description_public="مناسب نما و کف",
    )
    public_item = make_item(business, product=product, lot_code="PUB-1", b2b=B2B, b2c=B2C)
    hidden_item = make_item(
        business,
        product=product,
        lot_code="PRIV-1",
        is_visible=False,
        b2b=PRIVATE_B2B,
        b2c=PRIVATE_B2C,
    )
    return {
        "business": business,
        "membership": owner_membership(business),
        "public_lot": public_item,
        "private_lot": hidden_item,
    }


def _body(client, url) -> str:
    return client.get(url).content.decode("utf-8")


def _no_commas(text: str) -> str:
    return text.replace(",", "")


# --- price payload safety -----------------------------------------------------


@pytest.mark.django_db
def test_b2c_price_context_never_includes_b2b(seller_setup):
    ctx = b2c_price_context(seller_setup["public_lot"])
    assert ctx["has_price"] is True
    assert ctx["amount"] == Decimal(B2C)
    assert "b2b" not in ctx
    assert B2B not in str(ctx)


@pytest.mark.django_db
def test_storefront_hides_hidden_items_and_b2b_price(client, seller_setup):
    url = reverse("catalog:storefront", kwargs={"business_slug": seller_setup["business"].slug})
    content = _no_commas(_body(client, url))
    assert "تراورتن کرم دمو" in content
    assert B2C in content
    assert B2B not in content
    assert "PRIV-1" not in content


@pytest.mark.django_db
def test_public_detail_rejects_a_hidden_item(client, seller_setup):
    url = reverse(
        "catalog:lot_detail",
        kwargs={"business_slug": seller_setup["business"].slug, "lot_id": seller_setup["private_lot"].id},
    )
    response = client.get(url)
    assert response.status_code == 404
    content = _no_commas(response.content.decode("utf-8"))
    assert PRIVATE_B2B not in content
    assert PRIVATE_B2C not in content


@pytest.mark.django_db
def test_public_detail_shows_only_b2c(client, seller_setup):
    business = seller_setup["business"]
    lot = seller_setup["public_lot"]
    url = reverse("catalog:lot_detail", kwargs={"business_slug": business.slug, "lot_id": lot.id})

    content = _no_commas(_body(client, url))
    assert B2C in content
    assert B2B not in content
    assert "قیمت همکار" not in content


@pytest.mark.django_db
def test_the_product_page_cannot_record_an_inquiry_directly(client, seller_setup):
    """AUD-009. The most obvious button on the most visited public page used to
    post a name and a phone straight into ``create_inquiry``, so the seller
    received an unverified number, no quantity and no OTP — while the designed
    flow next to it asked for all three."""
    business = seller_setup["business"]
    lot = seller_setup["public_lot"]
    url = reverse("catalog:lot_detail", kwargs={"business_slug": business.slug, "lot_id": lot.id})

    post = client.post(url, {"name": "مشتری تست", "phone": "09123334455", "message": "لطفاً تماس بگیرید"})
    assert post.status_code == 405
    assert not Inquiry.objects.exists()


@pytest.mark.django_db
def test_the_product_page_button_starts_the_verified_flow(client, seller_setup):
    lot = seller_setup["public_lot"]
    response = client.post(reverse("catalog:inquiry_start", kwargs={"item_id": lot.id}))

    assert response.status_code == 302
    assert response.url == reverse("catalog:inquiry_review")
    assert not Inquiry.objects.exists()
    assert lot.product.commercial_name in _body(client, reverse("catalog:inquiry_review"))


# --- public search ------------------------------------------------------------


@pytest.mark.django_db
def test_public_search_needs_no_login_and_spans_sellers(client, seller_setup):
    other = make_business(name="سنگ دیگر", owner_phone="09125550009", city="یزد")
    make_item(other, product=make_product(other, commercial_name="گرانیت نطنز"), lot_code="OTH-1", b2c="900000")

    content = _body(client, reverse("catalog:public_search"))
    assert "تراورتن کرم دمو" in content
    assert "گرانیت نطنز" in content
    assert "PRIV-1" not in content


@pytest.mark.django_db
def test_public_search_filters_by_stone_type(client, seller_setup):
    other = make_business(name="سنگ دیگر", owner_phone="09125550010")
    make_item(
        other,
        product=make_product(other, commercial_name="گرانیت نطنز", stone_type="گرانیت"),
        lot_code="OTH-1",
        b2c="900000",
    )
    content = _body(client, reverse("catalog:public_search") + "?stone_type=گرانیت")
    assert "گرانیت نطنز" in content
    assert "تراورتن کرم دمو" not in content


# --- per-product share links --------------------------------------------------


@pytest.mark.django_db
def test_share_link_works_without_authentication(client, seller_setup):
    lot = seller_setup["public_lot"]
    response = client.get(f"/p/{lot.public_token}/")
    assert response.status_code == 200
    content = _no_commas(response.content.decode("utf-8"))
    assert "تراورتن کرم دمو" in content
    assert B2C in content
    assert B2B not in content


@pytest.mark.django_db
def test_share_link_of_a_colleague_never_shows_b2b_even_when_logged_in(client, seller_setup):
    """A share URL is B2C-safe by construction, whoever opens it."""
    colleague = make_business(name="همکار", owner_phone="09125550020")
    client.force_login(colleague.memberships.get(role="owner").user)

    content = _no_commas(_body(client, f"/p/{seller_setup['public_lot'].public_token}/"))
    assert B2B not in content
    assert B2C in content


@pytest.mark.django_db
def test_share_link_of_a_hidden_item_is_not_found(client, seller_setup):
    response = client.get(f"/p/{seller_setup['private_lot'].public_token}/")
    assert response.status_code == 404
    assert PRIVATE_B2C not in _no_commas(response.content.decode("utf-8"))


@pytest.mark.django_db
def test_share_link_of_an_unavailable_item_offers_no_purchase(client, seller_setup):
    lot = seller_setup["public_lot"]
    lot.availability_status = InventoryLot.Availability.UNAVAILABLE
    lot.save()

    response = client.get(f"/p/{lot.public_token}/")
    assert response.status_code == 404
    body = response.content.decode("utf-8")
    assert "موجود نیست" in body
    assert "درخواست استعلام" not in body


@pytest.mark.django_db
def test_share_link_of_a_deleted_item_leaks_nothing(client, seller_setup):
    lot = seller_setup["public_lot"]
    lot.deleted_at = timezone.now()
    lot.save()

    response = client.get(f"/p/{lot.public_token}/")
    assert response.status_code == 404
    assert "تراورتن کرم دمو" not in response.content.decode("utf-8")


@pytest.mark.django_db
def test_share_page_shows_stock_and_price_inquiry_when_stale(client, seller_setup):
    lot = seller_setup["public_lot"]
    expire_stock(lot)
    expire_price(lot, "b2c")

    content = _no_commas(_body(client, f"/p/{lot.public_token}/"))
    assert "استعلام موجودی" in content
    assert "استعلام قیمت" in content
    assert B2C not in content


@pytest.mark.django_db
def test_open_graph_metadata_is_b2c_safe(client, seller_setup):
    content = _no_commas(_body(client, f"/p/{seller_setup['public_lot'].public_token}/"))
    assert 'property="og:title"' in content
    assert B2B not in content


# --- shared catalogs ----------------------------------------------------------


@pytest.mark.django_db
def test_shared_catalog_is_b2c_safe(client, seller_setup):
    catalog = create_custom_catalog(
        business=seller_setup["business"],
        membership=seller_setup["membership"],
        title="کاتالوگ نمای پروژه",
        customer_name="آقای رضایی",
        lot_ids=[seller_setup["public_lot"].id],
    )
    url = reverse("catalog:shared_catalog", kwargs={"share_token": catalog.share_token})
    content = _no_commas(_body(client, url))
    assert "کاتالوگ نمای پروژه" in content
    assert B2C in content
    assert B2B not in content
    catalog.refresh_from_db()
    assert catalog.view_count == 1


@pytest.mark.django_db
def test_shared_catalog_never_exposes_a_hidden_item(client, seller_setup):
    """P0 regression: a curated share link must not widen visibility."""
    catalog = create_custom_catalog(
        business=seller_setup["business"],
        membership=seller_setup["membership"],
        title="کاتالوگ نشتی",
        lot_ids=[seller_setup["public_lot"].id, seller_setup["private_lot"].id],
    )
    # Curating is a management action, so the seller may select either item.
    assert catalog.items.count() == 2

    url = reverse("catalog:shared_catalog", kwargs={"share_token": catalog.share_token})
    content = _no_commas(_body(client, url))
    assert "PRIV-1" not in content
    assert PRIVATE_B2C not in content
    assert PRIVATE_B2B not in content
    assert B2C in content


@pytest.mark.django_db
@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda lot: setattr(lot, "is_visible", False), id="hidden"),
        pytest.param(
            lambda lot: setattr(lot, "availability_status", InventoryLot.Availability.UNAVAILABLE),
            id="unavailable",
        ),
        pytest.param(lambda lot: setattr(lot, "status", InventoryLot.Status.DRAFT), id="draft"),
        pytest.param(lambda lot: setattr(lot, "deleted_at", timezone.now()), id="deleted"),
    ],
)
def test_catalog_drops_items_the_storefront_would_hide(client, seller_setup, mutate):
    """The share link and the storefront must agree on every exclusion rule."""
    lot = seller_setup["public_lot"]
    catalog = create_custom_catalog(
        business=seller_setup["business"],
        membership=seller_setup["membership"],
        title="کاتالوگ بررسی",
        lot_ids=[lot.id],
    )
    mutate(lot)
    lot.save()

    url = reverse("catalog:shared_catalog", kwargs={"share_token": catalog.share_token})
    content = _no_commas(_body(client, url))
    assert "PUB-1" not in content
    assert B2C not in content


@pytest.mark.django_db
def test_an_item_returns_to_the_catalog_when_it_becomes_available_again(client, seller_setup):
    """Catalog membership is evaluated live, so nothing has to be re-curated."""
    lot = seller_setup["public_lot"]
    catalog = create_custom_catalog(
        business=seller_setup["business"],
        membership=seller_setup["membership"],
        title="کاتالوگ برگشتی",
        lot_ids=[lot.id],
    )
    url = reverse("catalog:shared_catalog", kwargs={"share_token": catalog.share_token})

    lot.availability_status = InventoryLot.Availability.UNAVAILABLE
    lot.save()
    assert "PUB-1" not in _body(client, url)

    lot.availability_status = InventoryLot.Availability.AVAILABLE
    lot.save()
    assert "تراورتن کرم دمو" in _body(client, url)


# --- tenant isolation on curation ---------------------------------------------


@pytest.mark.django_db
def test_catalog_refuses_an_item_belonging_to_another_business(seller_setup):
    intruder = make_business(name="سنگ مزاحم", owner_phone="09125550002", city="تهران")
    intruder_item = make_item(intruder, lot_code="INT-1")

    catalog = create_custom_catalog(
        business=seller_setup["business"],
        membership=seller_setup["membership"],
        title="کاتالوگ سالم",
        lot_ids=[seller_setup["public_lot"].id],
    )

    with pytest.raises(CatalogError):
        set_catalog_lots(
            catalog=catalog,
            membership=seller_setup["membership"],
            lot_ids=[seller_setup["public_lot"].id, intruder_item.id],
        )
    assert list(catalog.items.values_list("lot_id", flat=True)) == [seller_setup["public_lot"].id]


@pytest.mark.django_db
def test_catalog_refuses_a_malformed_item_id(seller_setup):
    catalog = create_custom_catalog(
        business=seller_setup["business"],
        membership=seller_setup["membership"],
        title="کاتالوگ سالم",
        lot_ids=[seller_setup["public_lot"].id],
    )
    with pytest.raises(CatalogError):
        set_catalog_lots(catalog=catalog, membership=seller_setup["membership"], lot_ids=["not-a-uuid"])
    assert catalog.items.count() == 1


@pytest.mark.django_db
def test_creating_a_catalog_with_a_foreign_item_is_refused(seller_setup):
    intruder = make_business(name="سنگ مزاحم ۲", owner_phone="09125550003", city="تهران")
    intruder_item = make_item(intruder, lot_code="INT-2")

    with pytest.raises(CatalogError):
        create_custom_catalog(
            business=seller_setup["business"],
            membership=seller_setup["membership"],
            title="کاتالوگ آلوده",
            lot_ids=[intruder_item.id],
        )
    assert not CustomCatalog.objects.filter(title="کاتالوگ آلوده").exists()
