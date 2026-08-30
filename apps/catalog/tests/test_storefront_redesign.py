from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import StorefrontCollection
from apps.catalog.selectors import active_special_lots
from apps.catalog.services import (
    CatalogError,
    apply_storefront_suggestions,
    b2c_price_context,
    regenerate_storefront_token,
    save_storefront_collection,
    set_storefront_collection_lots,
)
from apps.core.testing import make_business, make_item, make_product, owner_membership
from apps.pricing.services import ensure_default_tiers, set_lot_price


@pytest.fixture
def storefront(db):
    ensure_default_tiers()
    seller = make_business(name="سنگ خصوصی", owner_phone="09351110001")
    first = make_item(
        seller,
        product=make_product(seller, commercial_name="تراورتن روشن"),
        lot_code="SF-001",
        b2b="850000",
        b2c="1250000",
    )
    second = make_item(
        seller,
        product=make_product(seller, commercial_name="مرمریت اقتصادی", stone_type="مرمریت"),
        lot_code="SF-002",
        b2c="900000",
    )
    other = make_business(name="سنگ دیگر", owner_phone="09351110002")
    foreign = make_item(other, lot_code="SF-X01", b2c="700000")
    return {
        "seller": seller,
        "membership": owner_membership(seller),
        "first": first,
        "second": second,
        "other": other,
        "foreign": foreign,
    }


def storefront_url(world):
    return reverse(
        "catalog:storefront",
        kwargs={"storefront_token": world["seller"].storefront_token},
    )


@pytest.mark.django_db
def test_token_storefront_is_login_free_noindex_and_seller_scoped(client, storefront):
    body = client.get(storefront_url(storefront)).content.decode()
    assert "تراورتن روشن" in body
    assert "مرمریت اقتصادی" in body
    assert "سنگ دیگر" not in body
    assert "SF-X01" not in body
    assert "850000" not in body.replace(",", "")
    assert 'name="robots" content="noindex,nofollow,noarchive"' in body
    assert "sale-showcase" not in body


@pytest.mark.django_db
def test_product_id_cannot_cross_the_storefront_boundary(client, storefront):
    url = reverse(
        "catalog:lot_detail",
        kwargs={
            "storefront_token": storefront["seller"].storefront_token,
            "lot_id": storefront["foreign"].pk,
        },
    )
    response = client.get(url)
    assert response.status_code == 404
    assert "سنگ دیگر" not in response.content.decode()


@pytest.mark.django_db
def test_cross_seller_inquiry_manipulation_is_rejected(client, storefront):
    url = reverse(
        "catalog:inquiry_start",
        kwargs={
            "storefront_token": storefront["seller"].storefront_token,
            "item_id": storefront["foreign"].pk,
        },
    )
    assert client.post(url).status_code == 404


@pytest.mark.django_db
def test_regenerating_storefront_token_revokes_old_link(client, storefront):
    old_url = storefront_url(storefront)
    old_token = storefront["seller"].storefront_token
    regenerate_storefront_token(
        business=storefront["seller"], membership=storefront["membership"]
    )
    storefront["seller"].refresh_from_db()
    assert storefront["seller"].storefront_token != old_token
    assert client.get(old_url).status_code == 404
    assert client.get(storefront_url(storefront)).status_code == 200


@pytest.mark.django_db
def test_special_sale_displays_original_discount_and_remaining_time(client, storefront):
    set_lot_price(
        lot=storefront["first"],
        tier_code="b2c",
        amount=Decimal("1250000"),
        special_amount=Decimal("1090000"),
        special_until=timezone.now() + timedelta(days=2, hours=6),
    )
    body = client.get(storefront_url(storefront)).content.decode().replace(",", "")
    assert "فروش ویژه" in body
    assert "1250000" in body
    assert "1090000" in body
    assert "13٪ تخفیف" in body
    assert "تا پایان پیشنهاد" in body


@pytest.mark.django_db
def test_specials_are_ordered_by_expiry_and_management_count_is_not_capped(client, storefront):
    now = timezone.now()
    lots = [storefront["first"], storefront["second"]]
    for index in range(11):
        lots.append(
            make_item(
                storefront["seller"],
                product=make_product(
                    storefront["seller"], commercial_name=f"پیشنهاد ویژه {index + 1}"
                ),
                lot_code=f"SF-S{index + 1:02d}",
                b2c=str(2_000_000 + index),
            )
        )
    for index, lot in enumerate(reversed(lots), start=1):
        set_lot_price(
            lot=lot,
            tier_code="b2c",
            amount=Decimal("2000000") + index,
            special_amount=Decimal("1500000") + index,
            special_until=now + timedelta(hours=index),
        )

    ordered = list(active_special_lots(storefront["seller"], limit=None))
    assert len(ordered) == 13
    assert ordered[0].pk == lots[-1].pk
    assert len(active_special_lots(storefront["seller"])) == 12

    client.force_login(storefront["membership"].user)
    response = client.get(reverse("catalog_manage:list"))
    assert response.context["special_count"] == 13
    assert len(response.context["special_cards"]) == 12
    assert "فروش‌های ویژه فعال" in response.content.decode()


@pytest.mark.django_db
def test_discount_context_rounds_from_prices(storefront):
    set_lot_price(
        lot=storefront["first"],
        tier_code="b2c",
        amount=Decimal("1250000"),
        special_amount=Decimal("1090000"),
        special_until=timezone.now() + timedelta(days=1),
    )
    storefront["first"].refresh_from_db()
    context = b2c_price_context(storefront["first"])
    assert context["regular_amount"] == Decimal("1250000")
    assert context["amount"] == Decimal("1090000")
    assert context["discount_percent"] == 13


@pytest.mark.django_db
def test_collection_membership_is_tenant_scoped_and_ordered(storefront):
    collection = save_storefront_collection(
        business=storefront["seller"],
        membership=storefront["membership"],
        title="پیشنهاد فروشنده",
        is_active=True,
        lot_ids=[storefront["second"].pk, storefront["first"].pk],
    )
    assert list(collection.items.values_list("lot_id", flat=True)) == [
        storefront["second"].pk,
        storefront["first"].pk,
    ]
    with pytest.raises(CatalogError):
        set_storefront_collection_lots(
            collection=collection,
            membership=storefront["membership"],
            lot_ids=[storefront["foreign"].pk],
        )
    assert list(collection.items.values_list("lot_id", flat=True)) == [
        storefront["second"].pk,
        storefront["first"].pk,
    ]


@pytest.mark.django_db
def test_hidden_collection_and_ineligible_products_do_not_render(client, storefront):
    visible = save_storefront_collection(
        business=storefront["seller"],
        membership=storefront["membership"],
        title="منتخب",
        is_active=True,
        lot_ids=[storefront["first"].pk],
    )
    save_storefront_collection(
        business=storefront["seller"],
        membership=storefront["membership"],
        title="پنهان",
        is_active=False,
        lot_ids=[storefront["second"].pk],
    )
    storefront["first"].is_visible = False
    storefront["first"].save(update_fields=["is_visible"])
    body = client.get(storefront_url(storefront)).content.decode()
    assert visible.title not in body
    assert "پنهان" not in body


@pytest.mark.django_db
def test_economic_suggestions_use_current_b2c_prices_and_remain_editable(storefront):
    collection = StorefrontCollection.objects.create(
        business=storefront["seller"],
        title="اقتصادی",
        suggestion_kind=StorefrontCollection.SuggestionKind.ECONOMIC,
    )
    apply_storefront_suggestions(collection=collection, membership=storefront["membership"])
    assert list(collection.items.values_list("lot_id", flat=True))[:2] == [
        storefront["second"].pk,
        storefront["first"].pk,
    ]
    collection.items.filter(lot=storefront["second"]).delete()
    assert not collection.items.filter(lot=storefront["second"]).exists()


@pytest.mark.django_db
def test_seller_can_create_edit_and_delete_collection_from_management_ui(client, storefront):
    client.force_login(storefront["membership"].user)
    create_url = reverse("catalog_manage:collection_create")
    response = client.post(
        create_url,
        {
            "title": "انتخاب پروژه",
            "description": "برای نمای پروژه",
            "is_active": "on",
            "suggestion_kind": "",
            "products": [str(storefront["first"].pk)],
        },
    )
    assert response.status_code == 302
    collection = StorefrontCollection.objects.get(title="انتخاب پروژه")
    assert collection.items.get().lot_id == storefront["first"].pk
    edit_url = reverse("catalog_manage:collection_edit", kwargs={"collection_id": collection.pk})
    client.post(
        edit_url,
        {
            "title": "انتخاب نهایی",
            "description": "",
            "suggestion_kind": "",
            "products": [str(storefront["second"].pk)],
        },
    )
    collection.refresh_from_db()
    assert collection.title == "انتخاب نهایی"
    assert collection.is_active is False
    assert collection.items.get().lot_id == storefront["second"].pk
    delete_url = reverse("catalog_manage:collection_delete", kwargs={"collection_id": collection.pk})
    assert client.post(delete_url).status_code == 302
    assert not StorefrontCollection.objects.filter(pk=collection.pk).exists()


@pytest.mark.django_db
def test_collection_edit_preserves_manual_product_order(client, storefront):
    collection = save_storefront_collection(
        business=storefront["seller"],
        membership=storefront["membership"],
        title="ترتیب دستی",
        is_active=True,
        lot_ids=[storefront["first"].pk, storefront["second"].pk],
    )
    client.force_login(storefront["membership"].user)
    edit_url = reverse(
        "catalog_manage:collection_edit", kwargs={"collection_id": collection.pk}
    )
    response = client.post(
        edit_url,
        {
            "title": "ترتیب دستی ویرایش‌شده",
            "description": "فقط توضیح تغییر کرده است",
            "is_active": "on",
            "suggestion_kind": "",
            "products": [str(storefront["second"].pk), str(storefront["first"].pk)],
        },
    )
    assert response.status_code == 302
    assert list(collection.items.order_by("sort_order").values_list("lot_id", flat=True)) == [
        storefront["first"].pk,
        storefront["second"].pk,
    ]
    response = client.get(edit_url)
    assert "جست‌وجوی نام یا کد محصول" in response.content.decode()
