"""Django Admin must not be the way around the domain rules.

Admin is a technical tool with full table access, and the invariants that make
SANGA's financial records trustworthy live in services. That leaves a gap: a
superuser could edit a finalized Trade's amount or delete an issued invoice, and
nothing the services enforce would notice.

The rule is the one the ledger already follows — commercial history is corrected
by cancellation and reversal, never by editing or deleting the original.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.admin.sites import site
from django.utils import timezone

from apps.businesses.models import Business
from apps.core.testing import make_business, make_item, make_product, owner_membership
from apps.invoicing.models import SalesInvoice
from apps.invoicing.services import cancel_invoice, issue_invoice
from apps.pricing.services import ensure_default_tiers
from apps.trading.models import Trade
from apps.trading.services import record_direct_sale


@pytest.fixture
def records(db):
    ensure_default_tiers()
    seller = make_business(name="سنگ ادمین", owner_phone="09441110001")
    buyer = make_business(name="سنگ خریدار ادمین", owner_phone="09441110002")
    item = make_item(
        seller,
        product=make_product(seller, commercial_name="تراورتن ادمین"),
        lot_code="AD-1",
        b2b="1000000",
    )
    trade = record_direct_sale(
        seller_business=seller,
        membership=owner_membership(seller),
        item=item,
        quantity_sqm=Decimal("10"),
        unit_price=Decimal("1000000"),
        buyer_business=buyer,
    )
    return {
        "seller": seller,
        "buyer": buyer,
        "trade": trade,
        "invoice": SalesInvoice.objects.get(trade=trade),
        "membership": owner_membership(seller),
    }


def _admin(model):
    return site._registry[model]


class _Request:
    """The admin permission hooks only read ``user``."""

    def __init__(self, user=None):
        self.user = user


# --- nothing commercial is deleted from admin ---------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("model", [Trade, SalesInvoice, Business])
def test_admin_refuses_to_delete_commercial_records(records, model):
    assert _admin(model).has_delete_permission(_Request(), None) is False


@pytest.mark.django_db
def test_a_trade_cannot_be_created_by_hand_in_admin(records):
    """A trade is a consequence of finalizing a sale, never something typed in."""
    assert _admin(Trade).has_add_permission(_Request()) is False


# --- history is read-only ------------------------------------------------------


@pytest.mark.django_db
def test_every_field_of_a_trade_is_read_only(records):
    admin = _admin(Trade)
    readonly = set(admin.get_readonly_fields(_Request(), records["trade"]))
    for field in ("total_amount", "quantity_sqm", "unit_price", "product_name", "buyer_business"):
        assert field in readonly


@pytest.mark.django_db
def test_an_issued_invoice_is_read_only(records):
    invoice = records["invoice"]
    assert invoice.status == SalesInvoice.Status.ISSUED

    readonly = set(_admin(SalesInvoice).get_readonly_fields(_Request(), invoice))
    for field in ("status", "issue_date", "buyer_business", "notes"):
        assert field in readonly


@pytest.mark.django_db
def test_a_draft_invoice_stays_editable(records):
    """The lock is on documents that have been sent, not on ones being written."""
    invoice = SalesInvoice.objects.create(
        seller_business=records["seller"],
        buyer_business=records["buyer"],
        buyer_name=records["buyer"].name,
        issue_date=timezone.localdate(),
    )

    readonly = set(_admin(SalesInvoice).get_readonly_fields(_Request(), invoice))
    assert "notes" not in readonly
    assert "number" in readonly, "the allocated number is never editable"


@pytest.mark.django_db
def test_a_cancelled_invoice_is_read_only_too(records):
    invoice = records["invoice"]
    issue_invoice(invoice=invoice, membership=records["membership"])
    cancel_invoice(
        invoice=invoice,
        membership=records["membership"],
        reason="ابطال آزمون ادمین",
    )

    readonly = set(_admin(SalesInvoice).get_readonly_fields(_Request(), invoice))
    assert "status" in readonly


@pytest.mark.django_db
def test_history_can_still_be_opened_and_read(records):
    """Read-only, not hidden. Operators looking at a dispute need to see it."""
    from django.contrib.auth import get_user_model

    superuser = get_user_model().objects.create_superuser(phone="09441119999", password="not-used-for-otp-login")
    assert _admin(Trade).has_change_permission(_Request(superuser), records["trade"]) is True


# --- retired concepts are gone from admin ---------------------------------------


@pytest.mark.django_db
def test_warehouse_is_not_registered_in_admin():
    """The workflow was removed in V2; the model survives only to keep the
    migration graph whole. Leaving it in admin invited operators to create
    records nothing reads."""
    from apps.businesses.models import Warehouse

    assert Warehouse not in site._registry
