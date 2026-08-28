from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.urls import reverse

from apps.core.testing import make_business, owner_membership
from apps.invoicing.documents import build_preview_document
from apps.invoicing.forms import ManualInvoiceForm
from apps.invoicing.models import LocalCounterparty, SalesInvoice
from apps.invoicing.partner_services import update_partner_draft
from apps.invoicing.services import create_manual_invoice, update_draft_invoice


def _header(mode: str, **overrides) -> dict:
    values = {
        "counterparty_mode": mode,
        "issue_date": "2026-08-28",
        "currency": "IRR",
        "display_unit": "IRR",
        "invoice_discount_type": "none",
        "invoice_discount_value": "0",
        "tax_amount": "0",
        "shipping_amount": "0",
        "adjustment_amount": "0",
        "paid_amount": "0",
        "settlement_method": "credit",
        "cash_amount": "0",
        "credit_amount": "100",
        "cheque_amount": "0",
        "palette": "forest",
        "primary_color": "#1f513c",
        "header_style": "modern",
        "logo_size": "medium",
    }
    values.update(overrides)
    return values


def _lines() -> list[dict]:
    return [
        {
            "product_name": "تراورتن طولانی برای آزمون حالت خریدار",
            "quantity": Decimal("1"),
            "unit": "متر مربع",
            "unit_price": Decimal("100"),
            "item": None,
        }
    ]


@pytest.mark.django_db
def test_customer_mode_ignores_stale_partner_fields_before_field_validation():
    seller = make_business(name="فروشنده فرم", owner_phone="09120001001")
    form = ManualInvoiceForm(
        data=_header(
            SalesInvoice.Counterparty.CUSTOMER,
            customer_name="مشتری نهایی",
            buyer_business="not-a-valid-id",
            local_counterparty="not-a-valid-id",
            local_name="داده قدیمی",
            cheque_reference="OLD-CHEQUE",
        ),
        business=seller,
    )

    assert form.is_valid(), form.errors
    assert form.cleaned_data["buyer_business"] is None
    assert form.cleaned_data["local_counterparty"] is None
    assert form.cleaned_data["local_name"] is None
    assert form.cleaned_data["settlement_method"] is None
    assert form.cleaned_data["cheque_reference"] is None


@pytest.mark.django_db
def test_registered_partner_mode_clears_customer_and_local_values():
    seller = make_business(name="فروشنده همکار", owner_phone="09120001002")
    buyer = make_business(name="همکار ثبت‌شده", owner_phone="09120001003")
    form = ManualInvoiceForm(
        data=_header(
            SalesInvoice.Counterparty.BUSINESS,
            buyer_business=str(buyer.pk),
            customer_name="مشتری قدیمی",
            customer_phone="09121111111",
            buyer_address="آدرس قدیمی",
            local_counterparty="invalid-local-id",
            local_name="همکار محلی قدیمی",
            paid_amount="99",
            cheque_reference="OLD-CHEQUE",
        ),
        business=seller,
    )

    assert form.is_valid(), form.errors
    assert form.cleaned_data["buyer_business"] == buyer
    assert form.cleaned_data["customer_name"] is None
    assert form.cleaned_data["customer_phone"] is None
    assert form.cleaned_data["buyer_address"] is None
    assert form.cleaned_data["paid_amount"] is None
    assert form.cleaned_data["local_name"] is None
    assert form.cleaned_data["cheque_reference"] is None


@pytest.mark.django_db
def test_invalid_registered_partner_submission_preserves_mode_and_scopes_errors():
    seller = make_business(name="فروشنده خطای فرم", owner_phone="09120001009")
    form = ManualInvoiceForm(
        data=_header(
            SalesInvoice.Counterparty.BUSINESS,
            customer_name="مقدار نامرتبط",
            local_name="مقدار نامرتبط",
        ),
        business=seller,
    )

    assert not form.is_valid()
    assert form["counterparty_mode"].value() == SalesInvoice.Counterparty.BUSINESS
    assert list(form.errors) == ["buyer_business"]


@pytest.mark.django_db
def test_existing_local_partner_suppresses_new_local_fields():
    seller = make_business(name="فروشنده محلی", owner_phone="09120001004")
    member = owner_membership(seller)
    local = LocalCounterparty.objects.create(
        owner_business=seller,
        name="همکار محلی موجود",
        phone="09122222222",
        created_by=member.user,
    )
    form = ManualInvoiceForm(
        data=_header(
            SalesInvoice.Counterparty.LOCAL,
            local_counterparty=str(local.pk),
            local_name="همکار محلی تکراری",
            local_phone="09123333333",
            local_address="آدرس تکراری",
            customer_name="مشتری قدیمی",
        ),
        business=seller,
    )

    assert form.is_valid(), form.errors
    assert form.cleaned_data["local_counterparty"] == local
    assert form.cleaned_data["local_name"] is None
    assert form.cleaned_data["local_phone"] is None
    assert form.cleaned_data["local_address"] is None
    assert form.cleaned_data["customer_name"] is None


@pytest.mark.django_db
def test_preview_uses_the_active_registered_partner_snapshot():
    seller = make_business(name="فروشنده پیش‌نمایش", owner_phone="09120001005")
    buyer = make_business(name="همکار پیش‌نمایش", owner_phone="09120001006")
    totals = SimpleNamespace(
        amount_due=Decimal("100"),
        gross_subtotal=Decimal("100"),
        line_discount_total=Decimal("0"),
        net_items_total=Decimal("100"),
        invoice_discount_amount=Decimal("0"),
        tax_amount=Decimal("0"),
        shipping_amount=Decimal("0"),
        adjustment_amount=Decimal("0"),
        total_amount=Decimal("100"),
        paid_amount=Decimal("0"),
    )

    document = build_preview_document(
        business=seller,
        header={
            **_header(SalesInvoice.Counterparty.BUSINESS),
            "buyer_business": buyer,
            "customer_name": "نباید نمایش داده شود",
        },
        calculated=[],
        totals=totals,
    )

    assert document["buyer"] == {
        "name": buyer.name,
        "phone": buyer.phone,
        "address": buyer.address,
    }


@pytest.mark.django_db
def test_live_preview_endpoint_excludes_stale_customer_identity(client):
    seller = make_business(name="فروشنده مسیر پیش‌نمایش", owner_phone="09120001010")
    buyer = make_business(name="همکار فعال پیش‌نمایش", owner_phone="09120001011")
    member = owner_membership(seller)
    client.force_login(member.user)
    session = client.session
    session["current_business_id"] = str(seller.id)
    session.save()
    payload = _header(
        SalesInvoice.Counterparty.BUSINESS,
        buyer_business=str(buyer.pk),
        customer_name="مشتری قدیمی نباید نمایش داده شود",
        **{
            "lines-TOTAL_FORMS": "1",
            "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "0",
            "lines-MAX_NUM_FORMS": "100",
            "lines-0-product_name": "سنگ پیش‌نمایش",
            "lines-0-quantity": "1",
            "lines-0-unit": "متر مربع",
            "lines-0-unit_price": "100",
            "lines-0-discount_type": "none",
            "lines-0-discount_value": "0",
            "lines-0-ORDER": "0",
        },
    )

    response = client.post(reverse("invoicing:preview"), payload)
    body = response.content.decode()

    assert response.status_code == 200
    assert buyer.name in body
    assert "مشتری قدیمی نباید نمایش داده شود" not in body


@pytest.mark.django_db
def test_draft_can_switch_between_customer_and_registered_partner_without_stale_identity():
    seller = make_business(name="فروشنده تبدیل", owner_phone="09120001007")
    buyer = make_business(name="همکار تبدیل", owner_phone="09120001008")
    member = owner_membership(seller)
    draft = create_manual_invoice(
        business=seller,
        membership=member,
        lines=_lines(),
        customer_name="مشتری اولیه",
        issue=False,
    )

    draft = update_partner_draft(
        invoice=draft,
        membership=member,
        lines=_lines(),
        buyer_business=buyer,
        local_counterparty=None,
        expected_version=draft.version,
        settlement_method=SalesInvoice.SettlementMethod.CREDIT,
        cash_amount=0,
        credit_amount=100,
        cheque_amount=0,
    )
    assert draft.counterparty_type == SalesInvoice.Counterparty.BUSINESS
    assert draft.buyer_business == buyer
    assert draft.local_counterparty is None
    assert draft.customer_name == ""
    assert draft.buyer_name == buyer.name

    draft = update_draft_invoice(
        invoice=draft,
        membership=member,
        lines=_lines(),
        expected_version=draft.version,
        customer_name="مشتری جایگزین",
        customer_phone="09124444444",
        buyer_address="آدرس مشتری جایگزین",
        paid_amount=0,
    )
    assert draft.counterparty_type == SalesInvoice.Counterparty.CUSTOMER
    assert draft.buyer_business is None
    assert draft.local_counterparty is None
    assert draft.customer_name == "مشتری جایگزین"
    assert draft.credit_amount == 0
