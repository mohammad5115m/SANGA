from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.accounting.models import LedgerEntry
from apps.accounting.selectors import (
    accepted_offer_for,
    current_balance,
    suggested_amount_for_offer,
    suggested_contact_for_offer,
    trade_entry_for_offer,
)
from apps.accounting.services import (
    LedgerDuplicateError,
    LedgerError,
    post_trade_entry,
    reverse_entry,
)
from apps.businesses.models import BusinessMembership
from apps.businesses.services import create_business_for_owner
from apps.contacts.services import ContactError, create_contact
from apps.core.testing import make_item, make_product
from apps.pricing.services import ensure_default_tiers
from apps.purchase_requests.models import PurchaseOffer
from apps.purchase_requests.services import (
    create_purchase_request,
    decide_offer,
    submit_private_offer,
)

User = get_user_model()

SALE = LedgerEntry.Type.SALE
PURCHASE = LedgerEntry.Type.PURCHASE


@pytest.fixture
def trade(db):
    ensure_default_tiers()
    seller_user = User.objects.create_user(phone="09120000301", full_name="فروشنده")
    buyer_user = User.objects.create_user(phone="09120000302", full_name="خریدار")
    staff_user = User.objects.create_user(phone="09120000303", full_name="کارمند فروشنده")
    other_user = User.objects.create_user(phone="09120000304", full_name="کسب‌وکار سوم")

    seller = create_business_for_owner(owner=seller_user, name="سنگ فروشنده", city="محلات")
    buyer = create_business_for_owner(owner=buyer_user, name="سنگ خریدار", city="تهران")
    other = create_business_for_owner(owner=other_user, name="سنگ غریبه", city="یزد")

    seller_m = BusinessMembership.objects.get(user=seller_user, business=seller)
    buyer_m = BusinessMembership.objects.get(user=buyer_user, business=buyer)
    other_m = BusinessMembership.objects.get(user=other_user, business=other)
    # Staff has ledger.view but not ledger.manage by role default.
    staff_m = BusinessMembership.objects.create(
        user=staff_user,
        business=seller,
        role=BusinessMembership.Role.STAFF,
        status=BusinessMembership.Status.ACTIVE,
    )

    product = make_product(seller, commercial_name="تراورتن عباس‌آباد")
    lot = make_item(seller, product=product, lot_code="TRD-1", b2b="1000000", b2c="1500000")

    seller_contact = create_contact(
        business=seller, membership=seller_m, display_name="سنگ خریدار"
    )
    buyer_contact = create_contact(
        business=buyer, membership=buyer_m, display_name="سنگ فروشنده"
    )
    return {
        "seller": seller,
        "buyer": buyer,
        "other": other,
        "seller_user": seller_user,
        "buyer_user": buyer_user,
        "staff_user": staff_user,
        "other_user": other_user,
        "seller_m": seller_m,
        "buyer_m": buyer_m,
        "staff_m": staff_m,
        "other_m": other_m,
        "lot": lot,
        "seller_contact": seller_contact,
        "buyer_contact": buyer_contact,
    }


def _accepted_offer(trade, *, unit_price=Decimal("1000000"), quantity=Decimal("40")) -> PurchaseOffer:
    """An accepted offer: the buyer posts demand, the seller quotes, the buyer accepts."""
    pr = create_purchase_request(
        business=trade["buyer"],
        membership=trade["buyer_m"],
        title="نیاز تراورتن پروژه",
        required_qty_sqm=quantity,
    )
    offer = submit_private_offer(
        purchase_request=pr,
        seller_business=trade["seller"],
        membership=trade["seller_m"],
        unit_price=unit_price,
        offered_qty_sqm=quantity,
        lot=trade["lot"],
    )
    decide_offer(offer=offer, membership=trade["buyer_m"], accept=True)
    offer.refresh_from_db()
    return offer


def _post(trade, offer=None, amount=Decimal("40000000"), **kwargs):
    params = {
        "business": trade["seller"],
        "contact": trade["seller_contact"],
        "membership": trade["seller_m"],
        "entry_type": SALE,
        "amount": amount,
        "related_offer": offer,
    }
    params.update(kwargs)
    return post_trade_entry(**params)


# --- manual recording ------------------------------------------------------


def test_manual_sale_posts_one_entry_with_a_positive_delta(trade):
    entry = _post(trade, description="فروش نقدی درب کارگاه")

    assert LedgerEntry.objects.filter(business=trade["seller"]).count() == 1
    assert entry.entry_type == SALE
    assert entry.amount == Decimal("40000000.00")
    # A sale increases what the contact owes us.
    assert entry.balance_delta == Decimal("40000000.00")
    assert entry.balance_after == Decimal("40000000.00")
    assert entry.related_offer_id is None
    assert current_balance(trade["seller"], trade["seller_contact"]) == Decimal("40000000.00")


def test_manual_purchase_posts_one_entry_with_a_negative_delta(trade):
    entry = _post(trade, entry_type=PURCHASE, description="خرید بار از همکار")

    assert entry.entry_type == PURCHASE
    assert entry.balance_delta == Decimal("-40000000.00")
    assert current_balance(trade["seller"], trade["seller_contact"]) == Decimal("-40000000.00")


def test_manual_trades_are_deliberately_not_deduplicated(trade):
    """Nothing outside the ledger identifies an offline trade, so two genuine
    trades with the same contact and amount must both be recordable.
    """
    _post(trade, amount=Decimal("10000000"))
    _post(trade, amount=Decimal("10000000"))

    assert LedgerEntry.objects.filter(business=trade["seller"]).count() == 2
    assert current_balance(trade["seller"], trade["seller_contact"]) == Decimal("20000000.00")


def test_a_related_lot_of_another_business_is_refused(trade):
    foreign_lot = make_item(
        trade["buyer"],
        product=make_product(trade["buyer"], commercial_name="سنگ خریدار", stone_type="گرانیت"),
        lot_code="BUY-9",
    )
    with pytest.raises(LedgerError):
        _post(trade, related_lot=foreign_lot)
    assert not LedgerEntry.objects.exists()


def test_only_trade_entry_types_are_accepted(trade):
    with pytest.raises(LedgerError):
        _post(trade, entry_type=LedgerEntry.Type.PAYMENT_RECEIVED)
    assert not LedgerEntry.objects.exists()


def test_ledger_manage_is_required_at_the_service_layer(trade):
    with pytest.raises(LedgerError):
        _post(trade, membership=trade["staff_m"])
    assert not LedgerEntry.objects.exists()


def test_cannot_post_against_another_business_contact(trade):
    with pytest.raises(LedgerError):
        _post(trade, contact=trade["buyer_contact"])
    assert not LedgerEntry.objects.exists()


def test_membership_of_another_business_is_refused(trade):
    with pytest.raises(LedgerError):
        _post(trade, membership=trade["buyer_m"])
    assert not LedgerEntry.objects.exists()


def test_amount_must_be_positive(trade):
    with pytest.raises(LedgerError):
        _post(trade, amount=Decimal("0"))
    assert not LedgerEntry.objects.exists()


# --- recording from an accepted offer --------------------------------------


def test_offer_trade_posts_once_and_keeps_the_offer_and_lot_links(trade):
    offer = _accepted_offer(trade)
    entry = _post(trade, offer, related_lot=trade["lot"])

    assert LedgerEntry.objects.filter(business=trade["seller"]).count() == 1
    assert entry.related_offer_id == offer.id
    assert entry.related_lot_id == trade["lot"].id
    # Description is generated when the user leaves it blank.
    assert entry.description
    assert trade_entry_for_offer(trade["seller"], offer) == entry


def test_second_post_for_the_same_offer_is_refused_and_leaves_one_entry(trade):
    offer = _accepted_offer(trade)
    _post(trade, offer)

    with pytest.raises(LedgerDuplicateError) as exc_info:
        _post(trade, offer)

    assert exc_info.value.message == "سند مالی این معامله قبلاً ثبت شده است."
    assert exc_info.value.existing is not None
    assert LedgerEntry.objects.filter(related_offer=offer).count() == 1


def test_retry_with_a_different_contact_still_creates_only_one_entry(trade):
    offer = _accepted_offer(trade)
    _post(trade, offer)
    other_contact = create_contact(
        business=trade["seller"],
        membership=trade["seller_m"],
        display_name="مخاطب دوم",
    )

    with pytest.raises(LedgerDuplicateError):
        _post(trade, offer, contact=other_contact)
    assert LedgerEntry.objects.filter(related_offer=offer).count() == 1


def test_db_constraint_rejects_a_second_trade_entry_for_the_offer(trade):
    offer = _accepted_offer(trade)
    _post(trade, offer)

    # Bypass the service entirely: the database itself must refuse.
    with pytest.raises(IntegrityError), transaction.atomic():
        LedgerEntry.objects.create(
            business=trade["seller"],
            contact=trade["seller_contact"],
            entry_type=SALE,
            amount=Decimal("1000.00"),
            balance_delta=Decimal("1000.00"),
            balance_after=Decimal("40001000.00"),
            occurred_on=timezone.localdate(),
            related_offer=offer,
        )


def test_both_sides_of_an_offer_record_their_own_entry(trade):
    """The constraint is scoped by business: the seller books a فروش and the buyer
    books the mirroring خرید, each in their own ledger.
    """
    offer = _accepted_offer(trade)
    sale = _post(trade, offer)
    purchase = post_trade_entry(
        business=trade["buyer"],
        contact=trade["buyer_contact"],
        membership=trade["buyer_m"],
        entry_type=PURCHASE,
        amount=Decimal("40000000"),
        related_offer=offer,
    )

    assert sale.balance_delta == -purchase.balance_delta
    assert current_balance(trade["seller"], trade["seller_contact"]) == Decimal("40000000.00")
    assert current_balance(trade["buyer"], trade["buyer_contact"]) == Decimal("-40000000.00")


def test_a_business_outside_the_offer_cannot_attach_it(trade):
    offer = _accepted_offer(trade)
    outsider_contact = create_contact(
        business=trade["other"],
        membership=trade["other_m"],
        display_name="مخاطب غریبه",
    )
    with pytest.raises(LedgerError):
        post_trade_entry(
            business=trade["other"],
            contact=outsider_contact,
            membership=trade["other_m"],
            entry_type=SALE,
            amount=Decimal("40000000"),
            related_offer=offer,
        )
    assert not LedgerEntry.objects.exists()


def test_an_offer_that_was_not_accepted_cannot_be_recorded(trade):
    pr = create_purchase_request(
        business=trade["buyer"],
        membership=trade["buyer_m"],
        title="درخواست باز",
        required_qty_sqm=Decimal("20"),
    )
    offer = submit_private_offer(
        purchase_request=pr,
        seller_business=trade["seller"],
        membership=trade["seller_m"],
        unit_price=Decimal("1000000"),
        offered_qty_sqm=Decimal("20"),
    )
    with pytest.raises(LedgerError):
        _post(trade, offer)
    assert not LedgerEntry.objects.exists()


# --- reversal and re-recording ---------------------------------------------


def test_reversal_of_a_trade_entry_is_allowed_and_balance_reconciles(trade):
    entry = _post(trade, _accepted_offer(trade))

    reversal = reverse_entry(entry=entry, membership=trade["seller_m"])
    assert reversal.entry_type == LedgerEntry.Type.REVERSAL
    assert reversal.balance_delta == -entry.balance_delta

    entries = LedgerEntry.objects.filter(business=trade["seller"], contact=trade["seller_contact"])
    total = entries.aggregate(total=Sum("balance_delta"))["total"]
    assert total == current_balance(trade["seller"], trade["seller_contact"]) == Decimal("0.00")
    assert entries.count() == 2


def test_reversed_trade_can_be_recorded_again_with_the_offer_link(trade):
    offer = _accepted_offer(trade)
    wrong = _post(trade, offer, amount=Decimal("99999999"))
    reverse_entry(entry=wrong, membership=trade["seller_m"])

    wrong.refresh_from_db()
    assert wrong.reversed_at is not None
    # The slot is free again, so the pre-check no longer reports a duplicate.
    assert trade_entry_for_offer(trade["seller"], offer) is None

    corrected = _post(trade, offer, amount=Decimal("40000000"), related_lot=trade["lot"])
    assert corrected.related_offer_id == offer.id
    assert corrected.related_lot_id == trade["lot"].id
    assert trade_entry_for_offer(trade["seller"], offer) == corrected


def test_balance_reconciles_after_reverse_and_repost(trade):
    offer = _accepted_offer(trade)
    wrong = _post(trade, offer, amount=Decimal("99999999"))
    reverse_entry(entry=wrong, membership=trade["seller_m"])
    _post(trade, offer, amount=Decimal("40000000"))

    entries = LedgerEntry.objects.filter(business=trade["seller"], contact=trade["seller_contact"])
    total = entries.aggregate(total=Sum("balance_delta"))["total"]
    assert entries.count() == 3
    assert total == current_balance(trade["seller"], trade["seller_contact"]) == Decimal("40000000.00")


def test_reposting_after_a_reversal_is_still_blocked_the_second_time(trade):
    offer = _accepted_offer(trade)
    wrong = _post(trade, offer, amount=Decimal("99999999"))
    reverse_entry(entry=wrong, membership=trade["seller_m"])
    _post(trade, offer, amount=Decimal("40000000"))

    with pytest.raises(LedgerDuplicateError):
        _post(trade, offer, amount=Decimal("40000000"))
    assert LedgerEntry.objects.filter(related_offer=offer, entry_type=SALE).count() == 2


def test_db_constraint_still_rejects_a_second_live_trade_entry_after_a_reversal(trade):
    offer = _accepted_offer(trade)
    wrong = _post(trade, offer, amount=Decimal("99999999"))
    reverse_entry(entry=wrong, membership=trade["seller_m"])
    _post(trade, offer, amount=Decimal("40000000"))

    # Bypass the service: the partial unique index must still refuse a second
    # un-reversed trade entry for the same offer.
    with pytest.raises(IntegrityError), transaction.atomic():
        LedgerEntry.objects.create(
            business=trade["seller"],
            contact=trade["seller_contact"],
            entry_type=SALE,
            amount=Decimal("1000.00"),
            balance_delta=Decimal("1000.00"),
            balance_after=Decimal("40001000.00"),
            occurred_on=timezone.localdate(),
            related_offer=offer,
        )


def test_an_entry_cannot_be_reversed_twice(trade):
    entry = _post(trade)
    reverse_entry(entry=entry, membership=trade["seller_m"])

    with pytest.raises(LedgerError):
        reverse_entry(entry=entry, membership=trade["seller_m"])
    assert LedgerEntry.objects.filter(reverses=entry).count() == 1


def test_a_reversal_cannot_itself_be_reversed(trade):
    entry = _post(trade)
    reversal = reverse_entry(entry=entry, membership=trade["seller_m"])

    with pytest.raises(LedgerError):
        reverse_entry(entry=reversal, membership=trade["seller_m"])
    reversal.refresh_from_db()
    assert reversal.reversed_at is None


# --- selectors -------------------------------------------------------------


def test_suggested_amount_is_unit_price_times_offered_quantity(trade):
    offer = _accepted_offer(trade, unit_price=Decimal("1000000"), quantity=Decimal("12.5"))
    assert suggested_amount_for_offer(offer) == Decimal("12500000.00")


def test_suggested_amount_is_blank_for_a_zero_priced_offer(trade):
    offer = _accepted_offer(trade, unit_price=Decimal("0"))
    assert suggested_amount_for_offer(offer) is None


def test_suggested_contact_only_when_the_link_is_unambiguous(trade):
    offer = _accepted_offer(trade)
    assert suggested_contact_for_offer(trade["seller"], offer) is None

    linked = create_contact(
        business=trade["seller"],
        membership=trade["seller_m"],
        display_name="خریدار (متصل)",
        linked_business=trade["buyer"],
    )
    assert suggested_contact_for_offer(trade["seller"], offer) == linked

    # A second contact linked to the same buyer is refused outright, so the
    # suggestion can never become ambiguous through this route.
    with pytest.raises(ContactError):
        create_contact(
            business=trade["seller"],
            membership=trade["seller_m"],
            display_name="خریدار (متصل دوم)",
            linked_business=trade["buyer"],
        )
    assert suggested_contact_for_offer(trade["seller"], offer) == linked


def test_trade_entry_lookup_is_tenant_scoped(trade):
    offer = _accepted_offer(trade)
    _post(trade, offer)
    assert trade_entry_for_offer(trade["seller"], offer) is not None
    assert trade_entry_for_offer(trade["buyer"], offer) is None


def test_accepted_offer_lookup_is_limited_to_the_two_parties(trade):
    offer = _accepted_offer(trade)
    assert accepted_offer_for(trade["seller"], offer.id) == offer
    assert accepted_offer_for(trade["buyer"], offer.id) == offer
    assert accepted_offer_for(trade["other"], offer.id) is None


# --- the record-trade screen -----------------------------------------------

RECORD_URL = "/app/accounting/record-trade/"


def _record_url(offer=None) -> str:
    return f"{RECORD_URL}?offer={offer.id}" if offer is not None else RECORD_URL


def _record_payload(trade, amount="40000000", entry_type=SALE):
    return {
        "action": "record",
        "entry_type": entry_type,
        "contact": str(trade["seller_contact"].id),
        "amount": amount,
        "occurred_on": timezone.localdate().isoformat(),
        "description": "",
        "reference": "",
        "related_lot": "",
        "confirm": "on",
    }


def test_record_trade_screen_renders_without_an_offer(client, trade):
    client.force_login(trade["seller_user"])
    resp = client.get(RECORD_URL)
    assert resp.status_code == 200
    assert "ثبت سند مالی معامله" in resp.content.decode()


def test_record_trade_screen_renders_from_an_accepted_offer(client, trade):
    offer = _accepted_offer(trade)
    client.force_login(trade["seller_user"])
    body = client.get(_record_url(offer)).content.decode()
    assert "خلاصه پیشنهاد پذیرفته‌شده" in body
    assert trade["buyer"].name in body


def test_record_trade_view_posts_a_single_manual_entry(client, trade):
    client.force_login(trade["seller_user"])
    resp = client.post(RECORD_URL, _record_payload(trade))
    assert resp.status_code == 302
    entry = LedgerEntry.objects.get()
    assert entry.entry_type == SALE
    assert entry.related_offer_id is None


def test_record_trade_view_links_the_offer_it_was_started_from(client, trade):
    offer = _accepted_offer(trade)
    client.force_login(trade["seller_user"])
    resp = client.post(_record_url(offer), _record_payload(trade))
    assert resp.status_code == 302
    assert LedgerEntry.objects.filter(related_offer=offer).count() == 1


def test_record_trade_view_requires_confirmation(client, trade):
    client.force_login(trade["seller_user"])
    payload = _record_payload(trade)
    payload.pop("confirm")
    resp = client.post(RECORD_URL, payload)
    assert resp.status_code == 200
    assert not LedgerEntry.objects.exists()


def test_record_trade_view_tolerates_a_double_post_of_the_same_offer(client, trade):
    offer = _accepted_offer(trade)
    client.force_login(trade["seller_user"])
    client.post(_record_url(offer), _record_payload(trade))
    resp = client.post(_record_url(offer), _record_payload(trade), follow=True)

    assert resp.status_code == 200
    assert "سند مالی این معامله قبلاً ثبت شده است." in resp.content.decode()
    assert LedgerEntry.objects.filter(related_offer=offer).count() == 1


def test_record_trade_view_requires_ledger_manage(client, trade):
    client.force_login(trade["staff_user"])
    resp = client.get(RECORD_URL)
    assert resp.status_code == 302
    assert not LedgerEntry.objects.exists()


def test_record_trade_view_hides_an_offer_from_a_third_business(client, trade):
    offer = _accepted_offer(trade)
    client.force_login(trade["other_user"])
    assert client.get(_record_url(offer)).status_code == 404
    assert client.post(_record_url(offer), _record_payload(trade)).status_code == 404
    assert not LedgerEntry.objects.exists()


def test_record_trade_view_can_create_a_contact_inline(client, trade):
    offer = _accepted_offer(trade)
    client.force_login(trade["seller_user"])
    resp = client.post(
        _record_url(offer),
        {
            "offer": str(offer.id),
            "action": "create_contact",
            "display_name": "مشتری تازه",
            "phone": "09121110000",
        },
    )
    assert resp.status_code == 302
    assert "contact=" in resp["Location"]
    assert not LedgerEntry.objects.exists()
