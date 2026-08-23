from __future__ import annotations

import uuid
from decimal import Decimal

import pymupdf
import pytest
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.urls import reverse

from apps.core.persian import normalize_digits
from apps.core.testing import make_business, owner_membership
from apps.invoicing.calculations import calculate_invoice
from apps.invoicing.models import BusinessInvoiceSettings, SalesInvoice
from apps.invoicing.rendering import render_pdf
from apps.invoicing.services import (
    InvoiceError,
    create_manual_invoice,
    create_replacement_invoice,
    delete_draft_invoice,
    update_draft_invoice,
)
from apps.reporting.reports import DateRange, invoice_summary


@pytest.fixture
def seller(db):
    business = make_business(name="سنگ کنترل فاکتور", owner_phone="09501110001")
    return business, owner_membership(business)


def _lines(name: str = "تراورتن کنترل") -> list[dict]:
    return [
        {
            "product_name": name,
            "quantity": Decimal("2.125"),
            "unit_price": Decimal("1000"),
            "item": None,
        }
    ]


def _invoice(seller, **kwargs) -> SalesInvoice:
    values = {
        "business": seller[0],
        "membership": seller[1],
        "lines": _lines(),
        "customer_name": "مشتری کنترل",
    }
    values.update(kwargs)
    return create_manual_invoice(**values)


@pytest.mark.django_db
def test_manual_submission_token_is_idempotent(seller):
    token = uuid.uuid4()
    first = _invoice(seller, submission_id=token)
    second = _invoice(seller, submission_id=token)

    assert second.pk == first.pk
    assert SalesInvoice.objects.filter(seller_business=seller[0]).count() == 1


@pytest.mark.django_db
def test_lifecycle_audit_and_replacement_are_linked(seller):
    original = _invoice(seller)
    replacement = create_replacement_invoice(
        invoice=original,
        membership=seller[1],
        reason="اصلاح مقدار خریدار",
    )
    original.refresh_from_db()

    assert original.status == SalesInvoice.Status.CANCELLED
    assert original.cancel_reason == "اصلاح مقدار خریدار"
    assert original.cancelled_at is not None
    assert original.cancelled_by == seller[1].user
    assert replacement.status == SalesInvoice.Status.DRAFT
    assert replacement.replaces_invoice == original

    original.cancel_reason = "تلاش برای تغییر تاریخچه"
    with pytest.raises(ValidationError, match="تاریخچه ابطال"):
        original.save()


@pytest.mark.django_db
def test_stale_draft_update_is_rejected_and_draft_can_be_deleted(seller):
    draft = _invoice(seller, issue=False)

    with pytest.raises(InvoiceError, match="تغییر کرده"):
        update_draft_invoice(
            invoice=draft,
            membership=seller[1],
            lines=_lines(),
            expected_version=draft.version + 1,
        )

    delete_draft_invoice(invoice=draft, membership=seller[1])
    assert not SalesInvoice.objects.filter(pk=draft.pk).exists()


@pytest.mark.django_db
def test_browse_plan_cannot_open_settings_or_create_settings_row(client, seller):
    seller[0].plan = seller[0].Plan.BROWSE
    seller[0].save(update_fields=["plan"])
    client.force_login(seller[1].user)
    session = client.session
    session["current_business_id"] = str(seller[0].id)
    session.save()

    response = client.get(reverse("invoicing:settings"))

    assert response.status_code == 302
    assert not BusinessInvoiceSettings.objects.filter(business=seller[0]).exists()


@pytest.mark.django_db
def test_business_with_invoice_history_cannot_be_deleted(seller):
    _invoice(seller)

    with pytest.raises(ProtectedError):
        seller[0].delete()


def test_creditor_balance_reduces_amount_due_and_locale_number_is_normalized():
    _calculated, totals = calculate_invoice(
        [{"quantity": 1, "unit_price": 10_000}],
        previous_balance_snapshot=1_000,
        previous_balance_included=True,
        previous_balance_state="creditor",
    )

    assert totals.amount_due == Decimal("9000.00")
    assert normalize_digits("۱۲٬۳۴۵٫۶") == "12345.6"


@pytest.mark.django_db
def test_pdf_contains_complete_item_table(seller):
    invoice = _invoice(seller, issue=True)
    document = pymupdf.open(stream=render_pdf(invoice), filetype="pdf")
    text = "\n".join(page.get_text() for page in document)

    assert "تراورتن کنترل" in text
    assert "مقدار" in text
    assert "قیمت واحد" in text
    assert "مبلغ نهایی" in text
    assert "2.125" in text


@pytest.mark.django_db
def test_invoice_report_never_sums_different_currencies(seller):
    _invoice(seller, currency="IRR", display_unit="IRR")
    _invoice(seller, currency="USD", display_unit="USD")

    summary = invoice_summary(seller[0], DateRange())

    assert summary["total"] is None
    assert {row["currency"] for row in summary["totals"]} == {"IRR", "USD"}
