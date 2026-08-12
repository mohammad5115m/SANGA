"""What opening the network must NOT expose.

Every business now sees every other business's colleague-visible lots, with no
partnership of any kind. These tests pin the other half of that decision: a
business's contacts, ledger, balances, financial summary, aging report, private
lots and inquiries stay its own. None of it was ever gated by a partnership, so
none of it changed when partnerships were removed — this file proves that.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.accounting.reports import business_aging
from apps.accounting.selectors import (
    business_financial_summary,
    contact_balances,
    current_balance,
)
from apps.accounting.services import post_entry
from apps.businesses.models import BusinessMembership
from apps.businesses.services import add_warehouse, create_business_for_owner
from apps.contacts.selectors import contacts_for_business
from apps.contacts.services import create_contact
from apps.inquiries.models import Inquiry
from apps.inventory.models import InventoryLot, Product
from apps.marketplace.selectors import marketplace_lots_for
from apps.pricing.services import ensure_default_tiers, set_contact_price, set_lot_prices

User = get_user_model()

CONTACT_NAME = "مخاطب محرمانه آلفا"
LEDGER_AMOUNT = "77777777"
OVERRIDE_AMOUNT = "999999"
B2B_AMOUNT = "1111111"
B2C_AMOUNT = "2222222"


@pytest.fixture
def network(db):
    ensure_default_tiers()
    owner_a = User.objects.create_user(phone="09124440001", full_name="مالک آلفا")
    owner_b = User.objects.create_user(phone="09124440002", full_name="مالک بتا")
    biz_a = create_business_for_owner(owner=owner_a, name="سنگ آلفا", city="محلات")
    biz_b = create_business_for_owner(owner=owner_b, name="سنگ بتا", city="تهران")
    m_a = BusinessMembership.objects.get(user=owner_a, business=biz_a)
    m_b = BusinessMembership.objects.get(user=owner_b, business=biz_b)
    wh = add_warehouse(business=biz_a, name="انبار آلفا", is_default=True)
    product = Product.objects.create(
        business=biz_a, commercial_name="تراورتن آلفا", stone_type="تراورتن"
    )

    def make_lot(code, visibility):
        lot = InventoryLot.objects.create(
            business=biz_a,
            product=product,
            warehouse=wh,
            lot_code=code,
            status=InventoryLot.Status.AVAILABLE,
            visibility=visibility,
            available_sqm=Decimal("50"),
            original_sqm=Decimal("50"),
            inventory_confirmed_at=timezone.now(),
        )
        set_lot_prices(lot=lot, b2b_amount=Decimal(B2B_AMOUNT), b2c_amount=Decimal(B2C_AMOUNT))
        return lot

    public_lot = make_lot("PRIV-PUB", InventoryLot.Visibility.PUBLIC)
    colleagues_lot = make_lot("PRIV-COL", InventoryLot.Visibility.COLLEAGUES)
    private_lot = make_lot("PRIV-HID", InventoryLot.Visibility.PRIVATE)

    # A's own contact, linked to B, with a private price just for B.
    contact = create_contact(
        business=biz_a,
        membership=m_a,
        display_name=CONTACT_NAME,
        phone="09121112233",
        linked_business=biz_b,
    )
    set_contact_price(
        lot=public_lot, contact=contact, membership=m_a, amount=Decimal(OVERRIDE_AMOUNT)
    )
    post_entry(
        business=biz_a,
        contact=contact,
        membership=m_a,
        entry_type="sale",
        amount=Decimal(LEDGER_AMOUNT),
        description="فروش محرمانه",
    )
    Inquiry.objects.create(
        business=biz_a,
        lot=public_lot,
        name="مشتری نهایی",
        phone="09129998877",
        message="استعلام محرمانه",
    )

    return {
        "owner_a": owner_a,
        "owner_b": owner_b,
        "biz_a": biz_a,
        "biz_b": biz_b,
        "m_a": m_a,
        "m_b": m_b,
        "contact": contact,
        "public_lot": public_lot,
        "colleagues_lot": colleagues_lot,
        "private_lot": private_lot,
    }


def _login(client, user, business) -> None:
    client.force_login(user)
    session = client.session
    session["current_business_id"] = str(business.id)
    session.save()


def _clean(response) -> str:
    return response.content.decode("utf-8").replace(",", "")


@pytest.mark.django_db
def test_the_network_really_is_open(network):
    """The premise of the rest of this file: B sees A's colleague supply already."""
    codes = set(marketplace_lots_for(network["biz_b"]).values_list("lot_code", flat=True))
    assert codes == {"PRIV-COL", "PRIV-PUB"}


@pytest.mark.django_db
def test_public_storefront_is_public_only_and_carries_no_b2b_or_override(client, network):
    url = reverse("catalog:storefront", kwargs={"business_slug": network["biz_a"].slug})
    body = _clean(client.get(url))

    assert "PRIV-COL" not in body
    assert "PRIV-HID" not in body
    assert B2C_AMOUNT in body
    assert B2B_AMOUNT not in body
    assert OVERRIDE_AMOUNT not in body
    assert CONTACT_NAME not in body


@pytest.mark.django_db
def test_a_colleague_lot_is_not_on_the_public_storefront(client, network):
    url = reverse(
        "catalog:lot_detail",
        kwargs={"business_slug": network["biz_a"].slug, "lot_id": network["colleagues_lot"].id},
    )
    assert client.get(url).status_code == 404


@pytest.mark.django_db
def test_contacts_stay_inside_their_own_business(client, network):
    assert list(contacts_for_business(network["biz_b"])) == []

    _login(client, network["owner_b"], network["biz_b"])
    listing = client.get(reverse("contacts:list"))
    assert listing.status_code == 200
    assert CONTACT_NAME not in listing.content.decode("utf-8")

    detail = client.get(reverse("contacts:detail", kwargs={"contact_id": network["contact"].id}))
    assert detail.status_code == 404


@pytest.mark.django_db
def test_ledger_balances_and_summary_stay_inside_their_own_business(network):
    assert current_balance(network["biz_a"], network["contact"]) == Decimal("77777777.00")
    # From B's books the same contact simply does not exist.
    assert list(contact_balances(network["biz_b"])) == []
    assert current_balance(network["biz_b"], network["contact"]) == Decimal("0.00")

    summary_b = business_financial_summary(network["biz_b"])
    assert summary_b["receivable_total"] == Decimal("0.00")
    assert summary_b["net_balance"] == Decimal("0.00")
    assert summary_b["contact_count"] == 0


@pytest.mark.django_db
def test_ledger_screens_never_show_another_businesses_books(client, network):
    _login(client, network["owner_b"], network["biz_b"])

    index = client.get(reverse("accounting:index"))
    assert index.status_code == 200
    body = _clean(index)
    assert CONTACT_NAME not in body
    assert LEDGER_AMOUNT not in body

    aging = client.get(reverse("accounting:aging"))
    assert aging.status_code == 200
    assert CONTACT_NAME not in _clean(aging)

    statement = client.get(
        reverse("accounting:statement", kwargs={"contact_id": network["contact"].id})
    )
    assert statement.status_code == 404


@pytest.mark.django_db
def test_aging_report_is_scoped_to_the_owning_business(network):
    assert [row["contact"].id for row in business_aging(network["biz_a"])["rows"]] == [
        network["contact"].id
    ]
    assert business_aging(network["biz_b"])["rows"] == []


@pytest.mark.django_db
def test_inquiries_are_counted_only_for_the_business_that_received_them(client, network):
    """An inquiry on a lot B can see in the marketplace still belongs to A alone."""
    assert Inquiry.objects.filter(business=network["biz_b"]).count() == 0

    _login(client, network["owner_a"], network["biz_a"])
    dashboard = client.get(reverse("businesses:dashboard"))
    assert dashboard.context["unanswered_inquiry_count"] == 1

    _login(client, network["owner_b"], network["biz_b"])
    response = client.get(reverse("businesses:dashboard"))
    assert response.context["unanswered_inquiry_count"] == 0
    assert "استعلام محرمانه" not in response.content.decode("utf-8")
