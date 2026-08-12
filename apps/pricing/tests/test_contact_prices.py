from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.businesses.models import BusinessMembership
from apps.businesses.services import add_warehouse, create_business_for_owner
from apps.catalog.services import b2c_price_context
from apps.contacts.services import create_contact, restore_contact
from apps.inventory.models import InventoryLot, Product
from apps.marketplace.selectors import get_marketplace_lot, marketplace_lots_for
from apps.marketplace.services import b2b_price_context, marketplace_lot_card
from apps.pricing.models import ContactPrice
from apps.pricing.selectors import contact_price_count_for_contact
from apps.pricing.services import (
    PricingError,
    ensure_default_tiers,
    remove_contact_price,
    resolve_prices_for_viewer,
    set_contact_price,
    set_lot_prices,
)

User = get_user_model()

B2B_AMOUNT = Decimal("1500000")
B2C_AMOUNT = Decimal("2500000")
OVERRIDE_AMOUNT = Decimal("1200000")


@pytest.fixture
def pricing(db):
    """One seller, two colleague businesses, one lot priced at both tiers."""
    ensure_default_tiers()
    seller_user = User.objects.create_user(phone="09127770001", full_name="فروشنده")
    friend_user = User.objects.create_user(phone="09127770002", full_name="همکار ویژه")
    other_user = User.objects.create_user(phone="09127770003", full_name="همکار عادی")
    staff_user = User.objects.create_user(phone="09127770004", full_name="کارمند")

    seller = create_business_for_owner(owner=seller_user, name="سنگ فروشنده", city="محلات")
    friend = create_business_for_owner(owner=friend_user, name="همکار ویژه", city="تهران")
    other = create_business_for_owner(owner=other_user, name="همکار عادی", city="اصفهان")

    seller_m = BusinessMembership.objects.get(user=seller_user, business=seller)
    friend_m = BusinessMembership.objects.get(user=friend_user, business=friend)
    other_m = BusinessMembership.objects.get(user=other_user, business=other)
    # Staff has prices.view but not prices.edit by role default.
    staff_m = BusinessMembership.objects.create(
        user=staff_user,
        business=seller,
        role=BusinessMembership.Role.STAFF,
        status=BusinessMembership.Status.ACTIVE,
    )

    warehouse = add_warehouse(business=seller, name="انبار", is_default=True)
    product = Product.objects.create(
        business=seller, commercial_name="مرمریت ویژه", stone_type="مرمریت"
    )
    lot = InventoryLot.objects.create(
        business=seller,
        product=product,
        warehouse=warehouse,
        lot_code="CP-1",
        status=InventoryLot.Status.AVAILABLE,
        visibility=InventoryLot.Visibility.PUBLIC,
        available_sqm=Decimal("80"),
        original_sqm=Decimal("80"),
        inventory_confirmed_at=timezone.now(),
    )
    set_lot_prices(lot=lot, b2b_amount=B2B_AMOUNT, b2c_amount=B2C_AMOUNT)

    friend_contact = create_contact(
        business=seller,
        membership=seller_m,
        display_name="همکار ویژه",
        linked_business=friend,
    )
    plain_contact = create_contact(
        business=seller,
        membership=seller_m,
        display_name="مشتری بدون اتصال",
    )
    return {
        "seller": seller,
        "friend": friend,
        "other": other,
        "seller_user": seller_user,
        "friend_user": friend_user,
        "staff_user": staff_user,
        "seller_m": seller_m,
        "friend_m": friend_m,
        "other_m": other_m,
        "staff_m": staff_m,
        "lot": lot,
        "friend_contact": friend_contact,
        "plain_contact": plain_contact,
    }


def _set_override(pricing, amount=OVERRIDE_AMOUNT, **kwargs) -> ContactPrice:
    return set_contact_price(
        lot=pricing["lot"],
        contact=pricing["friend_contact"],
        membership=pricing["seller_m"],
        amount=amount,
        **kwargs,
    )


def _viewer_lot(pricing, viewer):
    """The lot as the marketplace hands it over, prefetches included."""
    return get_marketplace_lot(viewer, pricing["lot"].id)


# --- resolution ------------------------------------------------------------


def test_linked_colleague_sees_the_override_instead_of_the_b2b_tier(pricing):
    _set_override(pricing)
    lot = _viewer_lot(pricing, pricing["friend"])

    context = b2b_price_context(lot, pricing["friend"])
    assert context["amount"] == OVERRIDE_AMOUNT
    assert context["is_partner_price"] is True
    assert str(B2B_AMOUNT) not in str(context)


def test_a_different_colleague_still_sees_the_plain_b2b_tier(pricing):
    _set_override(pricing)
    lot = _viewer_lot(pricing, pricing["other"])

    context = b2b_price_context(lot, pricing["other"])
    assert context["amount"] == B2B_AMOUNT
    assert context["is_partner_price"] is False
    assert str(OVERRIDE_AMOUNT) not in str(context)


def test_the_public_b2c_catalog_never_sees_an_override(pricing):
    _set_override(pricing)
    lot = pricing["lot"]

    assert b2c_price_context(lot)["amount"] == B2C_AMOUNT
    # Even asked directly for the wrong audience, the override is withheld.
    for audience in ("b2c_public", "owner_staff", "platform_admin"):
        prices = resolve_prices_for_viewer(lot, audience, viewer_business=pricing["friend"])
        assert "contact" not in prices


def test_an_anonymous_viewer_never_sees_an_override(pricing):
    _set_override(pricing)
    prices = resolve_prices_for_viewer(pricing["lot"], "b2b_partner", viewer_business=None)
    assert "contact" not in prices
    assert b2b_price_context(pricing["lot"])["amount"] == B2B_AMOUNT


def test_public_storefront_page_does_not_leak_an_override(client, pricing):
    _set_override(pricing)
    response = client.get(
        reverse("catalog:storefront", kwargs={"business_slug": pricing["seller"].slug})
    )
    assert response.status_code == 200
    content = response.content.decode("utf-8").replace(",", "").replace("\u066c", "")
    assert str(OVERRIDE_AMOUNT) not in content


def test_marketplace_page_shows_the_override_to_the_linked_partner(client, pricing):
    _set_override(pricing)
    client.force_login(pricing["friend_user"])
    session = client.session
    session["current_business_id"] = str(pricing["friend"].id)
    session.save()

    response = client.get(reverse("marketplace:home"))
    assert response.status_code == 200
    content = response.content.decode("utf-8").replace(",", "").replace("\u066c", "")
    assert str(OVERRIDE_AMOUNT) in content
    assert str(B2B_AMOUNT) not in content


def test_a_lot_without_any_applicable_price_asks_the_viewer_to_enquire(pricing):
    pricing["lot"].prices.all().delete()
    lot = _viewer_lot(pricing, pricing["friend"])

    context = b2b_price_context(lot, pricing["friend"])
    assert context["has_price"] is False
    assert context["amount"] is None
    assert context["label"] == "استعلام بگیرید"


def test_an_inquiry_only_override_also_asks_the_viewer_to_enquire(pricing):
    _set_override(pricing, amount=None, unit="inquiry_only")
    lot = _viewer_lot(pricing, pricing["friend"])

    context = b2b_price_context(lot, pricing["friend"])
    assert context["has_price"] is False
    assert context["label"] == "استعلام بگیرید"
    # The override wins over the tier even when it says «no number».
    assert str(B2B_AMOUNT) not in str(context)


def test_an_override_on_an_unlinked_contact_reaches_nobody(pricing):
    set_contact_price(
        lot=pricing["lot"],
        contact=pricing["plain_contact"],
        membership=pricing["seller_m"],
        amount=OVERRIDE_AMOUNT,
    )
    for viewer in (pricing["friend"], pricing["other"]):
        lot = _viewer_lot(pricing, viewer)
        assert b2b_price_context(lot, viewer)["amount"] == B2B_AMOUNT


def test_archiving_the_contact_falls_back_to_the_b2b_tier(pricing):
    _set_override(pricing)
    contact = pricing["friend_contact"]
    contact.is_active = False
    contact.save(update_fields=["is_active"])

    lot = _viewer_lot(pricing, pricing["friend"])
    assert b2b_price_context(lot, pricing["friend"])["amount"] == B2B_AMOUNT


def test_removing_the_override_falls_back_to_the_b2b_tier(pricing):
    _set_override(pricing)
    remove_contact_price(
        lot=pricing["lot"],
        contact=pricing["friend_contact"],
        membership=pricing["seller_m"],
    )
    lot = _viewer_lot(pricing, pricing["friend"])
    assert b2b_price_context(lot, pricing["friend"])["amount"] == B2B_AMOUNT


def test_the_override_prefetch_costs_no_query_per_lot(pricing, django_assert_num_queries):
    _set_override(pricing)
    lots = list(marketplace_lots_for(pricing["friend"]))
    assert lots

    # One query for the lot list plus its prefetches, already spent above; the
    # card rendering itself must not touch the database again.
    with django_assert_num_queries(0):
        cards = [marketplace_lot_card(lot, pricing["friend"]) for lot in lots]
    assert cards[0]["price"]["amount"] == OVERRIDE_AMOUNT


# --- write path ------------------------------------------------------------


def test_prices_edit_is_required_at_the_service_layer(pricing):
    with pytest.raises(PricingError):
        set_contact_price(
            lot=pricing["lot"],
            contact=pricing["friend_contact"],
            membership=pricing["staff_m"],
            amount=OVERRIDE_AMOUNT,
        )
    assert not ContactPrice.objects.exists()


def test_prices_edit_is_required_to_remove_an_override(pricing):
    _set_override(pricing)
    with pytest.raises(PricingError):
        remove_contact_price(
            lot=pricing["lot"],
            contact=pricing["friend_contact"],
            membership=pricing["staff_m"],
        )
    assert ContactPrice.objects.count() == 1


def test_a_business_cannot_price_another_businesses_lot(pricing):
    intruder_contact = create_contact(
        business=pricing["friend"],
        membership=pricing["friend_m"],
        display_name="مخاطب همکار",
    )
    with pytest.raises(PricingError):
        set_contact_price(
            lot=pricing["lot"],
            contact=intruder_contact,
            membership=pricing["friend_m"],
            amount=OVERRIDE_AMOUNT,
        )
    assert not ContactPrice.objects.exists()


def test_a_business_cannot_price_against_another_businesses_contact(pricing):
    foreign_contact = create_contact(
        business=pricing["other"],
        membership=pricing["other_m"],
        display_name="مخاطب بیگانه",
    )
    with pytest.raises(PricingError):
        set_contact_price(
            lot=pricing["lot"],
            contact=foreign_contact,
            membership=pricing["seller_m"],
            amount=OVERRIDE_AMOUNT,
        )
    assert not ContactPrice.objects.exists()


def test_a_zero_or_invalid_override_is_refused(pricing):
    for amount in (Decimal("0"), Decimal("-1"), "abc"):
        with pytest.raises(PricingError):
            _set_override(pricing, amount=amount)
    assert not ContactPrice.objects.exists()


def test_setting_the_same_pair_twice_updates_rather_than_duplicates(pricing):
    _set_override(pricing)
    _set_override(pricing, amount=Decimal("1100000"))

    assert ContactPrice.objects.count() == 1
    assert ContactPrice.objects.get().amount == Decimal("1100000.00")


# --- management screen -----------------------------------------------------


def _prices_url(pricing) -> str:
    return reverse("inventory:lot_partner_prices", kwargs={"lot_id": pricing["lot"].id})


def test_management_screen_requires_prices_edit(client, pricing):
    client.force_login(pricing["staff_user"])
    response = client.get(_prices_url(pricing))
    assert response.status_code == 302


def test_owner_can_add_and_remove_an_override_from_the_screen(client, pricing):
    client.force_login(pricing["seller_user"])

    added = client.post(
        _prices_url(pricing),
        {
            "action": "save",
            "contact": str(pricing["friend_contact"].id),
            "amount": "1200000",
            "unit": "per_sqm",
            "currency": "IRR",
        },
    )
    assert added.status_code == 302
    assert ContactPrice.objects.count() == 1

    removed = client.post(
        _prices_url(pricing),
        {"action": "remove", "contact": str(pricing["friend_contact"].id)},
    )
    assert removed.status_code == 302
    assert not ContactPrice.objects.exists()


def test_management_screen_rejects_another_businesses_lot(client, pricing):
    client.force_login(pricing["friend_user"])
    session = client.session
    session["current_business_id"] = str(pricing["friend"].id)
    session.save()

    response = client.post(
        _prices_url(pricing),
        {
            "action": "save",
            "contact": str(pricing["friend_contact"].id),
            "amount": "1",
            "unit": "per_sqm",
            "currency": "IRR",
        },
    )
    assert response.status_code == 302
    assert not ContactPrice.objects.exists()


# --- archive warning -------------------------------------------------------

WARNING_MARKER = "قیمت توافقی ثبت شده است"


def _extra_lot(pricing, code: str) -> InventoryLot:
    lot = pricing["lot"]
    return InventoryLot.objects.create(
        business=lot.business,
        product=lot.product,
        warehouse=lot.warehouse,
        lot_code=code,
        status=InventoryLot.Status.AVAILABLE,
        visibility=InventoryLot.Visibility.PUBLIC,
        available_sqm=Decimal("40"),
        original_sqm=Decimal("40"),
        inventory_confirmed_at=timezone.now(),
    )


def _archive_url(contact) -> str:
    return reverse("contacts:archive", kwargs={"contact_id": contact.id})


def test_archive_screen_warns_with_the_number_of_negotiated_prices(client, pricing):
    _set_override(pricing)
    set_contact_price(
        lot=_extra_lot(pricing, "CP-2"),
        contact=pricing["friend_contact"],
        membership=pricing["seller_m"],
        amount=OVERRIDE_AMOUNT,
    )
    client.force_login(pricing["seller_user"])

    response = client.get(_archive_url(pricing["friend_contact"]))
    assert response.status_code == 200
    assert response.context["contact_price_count"] == 2
    content = response.content.decode("utf-8")
    assert WARNING_MARKER in content
    assert pricing["friend_contact"].display_name in content


def test_archive_screen_stays_silent_for_a_contact_without_overrides(client, pricing):
    _set_override(pricing)
    client.force_login(pricing["seller_user"])

    response = client.get(_archive_url(pricing["plain_contact"]))
    assert response.status_code == 200
    assert response.context["contact_price_count"] == 0
    assert WARNING_MARKER not in response.content.decode("utf-8")


def test_the_warning_count_never_crosses_a_tenant_boundary(pricing):
    _set_override(pricing)
    # The other business prices its own lot for its own contact.
    other_warehouse = add_warehouse(business=pricing["other"], name="انبار همکار", is_default=True)
    other_product = Product.objects.create(
        business=pricing["other"], commercial_name="تراورتن", stone_type="تراورتن"
    )
    other_lot = InventoryLot.objects.create(
        business=pricing["other"],
        product=other_product,
        warehouse=other_warehouse,
        lot_code="CP-OTHER",
        status=InventoryLot.Status.AVAILABLE,
        visibility=InventoryLot.Visibility.PUBLIC,
        available_sqm=Decimal("10"),
        original_sqm=Decimal("10"),
        inventory_confirmed_at=timezone.now(),
    )
    other_contact = create_contact(
        business=pricing["other"],
        membership=pricing["other_m"],
        display_name="مخاطب همکار عادی",
    )
    set_contact_price(
        lot=other_lot,
        contact=other_contact,
        membership=pricing["other_m"],
        amount=OVERRIDE_AMOUNT,
    )

    assert contact_price_count_for_contact(pricing["seller"], pricing["friend_contact"]) == 1
    assert contact_price_count_for_contact(pricing["other"], other_contact) == 1
    # A contact id from another tenant yields nothing, not that tenant's count.
    assert contact_price_count_for_contact(pricing["other"], pricing["friend_contact"]) == 0
    assert contact_price_count_for_contact(pricing["seller"], other_contact) == 0


def test_archiving_keeps_the_overrides_and_restoring_reapplies_them(client, pricing):
    _set_override(pricing)
    contact = pricing["friend_contact"]
    client.force_login(pricing["seller_user"])

    response = client.post(_archive_url(contact))
    assert response.status_code == 302
    contact.refresh_from_db()
    assert contact.is_active is False
    # Archiving is a warning, not a deletion: the rows survive.
    assert ContactPrice.objects.filter(contact=contact).count() == 1
    assert b2b_price_context(_viewer_lot(pricing, pricing["friend"]), pricing["friend"])[
        "amount"
    ] == B2B_AMOUNT

    restore_contact(contact=contact, membership=pricing["seller_m"])
    assert b2b_price_context(_viewer_lot(pricing, pricing["friend"]), pricing["friend"])[
        "amount"
    ] == OVERRIDE_AMOUNT


def test_archiving_a_contact_without_overrides_still_succeeds(client, pricing):
    contact = pricing["plain_contact"]
    client.force_login(pricing["seller_user"])

    response = client.post(_archive_url(contact))
    assert response.status_code == 302
    contact.refresh_from_db()
    assert contact.is_active is False
