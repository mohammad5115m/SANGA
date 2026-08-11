from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.businesses.models import BusinessMembership
from apps.businesses.services import add_warehouse, create_business_for_owner
from apps.catalog.models import CustomCatalog
from apps.catalog.services import (
    CatalogError,
    b2c_price_context,
    create_custom_catalog,
    set_catalog_lots,
)
from apps.inventory.models import InventoryLot, Product
from apps.inquiries.models import Inquiry
from apps.pricing.services import ensure_default_tiers, set_lot_prices

User = get_user_model()


@pytest.fixture
def seller_setup(db):
    owner = User.objects.create_user(phone="09125550001", full_name="فروشنده")
    business = create_business_for_owner(owner=owner, name="سنگسرا دمو", city="اصفهان")
    warehouse = add_warehouse(business=business, name="انبار ۱", is_default=True)
    membership = BusinessMembership.objects.get(user=owner, business=business)
    ensure_default_tiers()
    product = Product.objects.create(
        business=business,
        commercial_name="تراورتن کرم دمو",
        stone_type="تراورتن",
        primary_color="کرم",
        description_public="مناسب نما و کف",
    )
    public_lot = InventoryLot.objects.create(
        business=business,
        product=product,
        warehouse=warehouse,
        lot_code="PUB-1",
        status=InventoryLot.Status.AVAILABLE,
        visibility=InventoryLot.Visibility.CUSTOMER_CATALOG,
        available_sqm=Decimal("90"),
        original_sqm=Decimal("90"),
        inventory_confirmed_at=timezone.now(),
    )
    private_lot = InventoryLot.objects.create(
        business=business,
        product=product,
        warehouse=warehouse,
        lot_code="PRIV-1",
        status=InventoryLot.Status.AVAILABLE,
        visibility=InventoryLot.Visibility.PRIVATE,
        available_sqm=Decimal("40"),
        original_sqm=Decimal("40"),
        inventory_confirmed_at=timezone.now(),
    )
    set_lot_prices(
        lot=public_lot,
        b2b_amount=Decimal("1111111"),
        b2c_amount=Decimal("2222222"),
    )
    set_lot_prices(
        lot=private_lot,
        b2b_amount=Decimal("3333333"),
        b2c_amount=Decimal("4444444"),
    )
    return {
        "owner": owner,
        "business": business,
        "membership": membership,
        "public_lot": public_lot,
        "private_lot": private_lot,
    }


@pytest.mark.django_db
def test_b2c_price_context_never_includes_b2b(seller_setup):
    ctx = b2c_price_context(seller_setup["public_lot"])
    assert ctx["has_price"] is True
    assert ctx["amount"] == Decimal("2222222")
    assert "b2b" not in ctx
    assert "1111111" not in str(ctx)


@pytest.mark.django_db
def test_storefront_hides_private_and_b2b_price(client, seller_setup):
    business = seller_setup["business"]
    url = reverse("catalog:storefront", kwargs={"business_slug": business.slug})
    response = client.get(url)
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "تراورتن کرم دمو" in content
    assert "2222222" in content.replace(",", "")
    assert "1111111" not in content.replace(",", "")
    assert "PRIV-1" not in content


@pytest.mark.django_db
def test_public_lot_detail_rejects_private_lot(client, seller_setup):
    business = seller_setup["business"]
    private = seller_setup["private_lot"]
    url = reverse(
        "catalog:lot_detail",
        kwargs={"business_slug": business.slug, "lot_id": private.id},
    )
    response = client.get(url)
    assert response.status_code == 404
    content = response.content.decode("utf-8")
    assert "3333333" not in content
    assert "4444444" not in content


@pytest.mark.django_db
def test_public_lot_detail_shows_only_b2c_and_accepts_inquiry(client, seller_setup):
    business = seller_setup["business"]
    lot = seller_setup["public_lot"]
    url = reverse("catalog:lot_detail", kwargs={"business_slug": business.slug, "lot_id": lot.id})
    response = client.get(url)
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "2222222" in content.replace(",", "")
    assert "1111111" not in content.replace(",", "")
    assert "قیمت همکار" not in content
    assert "B2B" not in content

    post = client.post(
        url,
        {"name": "مشتری تست", "phone": "09123334455", "message": "لطفاً تماس بگیرید"},
    )
    assert post.status_code == 200
    assert Inquiry.objects.filter(business=business, lot=lot, phone="09123334455").exists()


@pytest.mark.django_db
def test_shared_catalog_is_b2c_safe(client, seller_setup):
    business = seller_setup["business"]
    membership = seller_setup["membership"]
    lot = seller_setup["public_lot"]
    catalog = create_custom_catalog(
        business=business,
        membership=membership,
        title="کاتالوگ نمای پروژه",
        customer_name="آقای رضایی",
        lot_ids=[lot.id],
    )
    url = reverse("catalog:shared_catalog", kwargs={"share_token": catalog.share_token})
    response = client.get(url)
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "کاتالوگ نمای پروژه" in content
    assert "2222222" in content.replace(",", "")
    assert "1111111" not in content.replace(",", "")
    catalog.refresh_from_db()
    assert catalog.view_count == 1


@pytest.mark.django_db
def test_catalog_refuses_a_lot_belonging_to_another_business(seller_setup):
    intruder_owner = User.objects.create_user(phone="09125550002", full_name="مزاحم")
    intruder = create_business_for_owner(owner=intruder_owner, name="سنگ مزاحم", city="تهران")
    intruder_lot = InventoryLot.objects.create(
        business=intruder,
        product=Product.objects.create(
            business=intruder, commercial_name="گرانیت مزاحم", stone_type="گرانیت"
        ),
        warehouse=add_warehouse(business=intruder, name="انبار مزاحم", is_default=True),
        lot_code="INT-1",
        status=InventoryLot.Status.AVAILABLE,
        available_sqm=Decimal("10"),
        original_sqm=Decimal("10"),
    )
    catalog = create_custom_catalog(
        business=seller_setup["business"],
        membership=seller_setup["membership"],
        title="کاتالوگ سالم",
        lot_ids=[seller_setup["public_lot"].id],
    )

    # A crafted lot id must be refused outright, not quietly dropped.
    with pytest.raises(CatalogError):
        set_catalog_lots(
            catalog=catalog,
            membership=seller_setup["membership"],
            lot_ids=[seller_setup["public_lot"].id, intruder_lot.id],
        )
    assert list(catalog.items.values_list("lot_id", flat=True)) == [seller_setup["public_lot"].id]


@pytest.mark.django_db
def test_catalog_refuses_a_malformed_lot_id(seller_setup):
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
            lot_ids=["not-a-uuid"],
        )
    assert catalog.items.count() == 1


@pytest.mark.django_db
def test_creating_a_catalog_with_a_foreign_lot_is_refused(seller_setup):
    intruder_owner = User.objects.create_user(phone="09125550003", full_name="مزاحم دوم")
    intruder = create_business_for_owner(owner=intruder_owner, name="سنگ مزاحم ۲", city="تهران")
    intruder_lot = InventoryLot.objects.create(
        business=intruder,
        product=Product.objects.create(
            business=intruder, commercial_name="گرانیت مزاحم", stone_type="گرانیت"
        ),
        warehouse=add_warehouse(business=intruder, name="انبار مزاحم ۲", is_default=True),
        lot_code="INT-2",
        status=InventoryLot.Status.AVAILABLE,
        available_sqm=Decimal("10"),
        original_sqm=Decimal("10"),
    )
    with pytest.raises(CatalogError):
        create_custom_catalog(
            business=seller_setup["business"],
            membership=seller_setup["membership"],
            title="کاتالوگ آلوده",
            lot_ids=[intruder_lot.id],
        )
    assert not CustomCatalog.objects.filter(title="کاتالوگ آلوده").exists()
