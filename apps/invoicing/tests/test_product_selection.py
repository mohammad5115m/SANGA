from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

import pytest
from django.conf import settings
from django.urls import reverse

from apps.core.testing import make_business, make_item, owner_membership
from apps.invoicing.forms import BusinessInvoiceSettingsForm, ChequeStatusForm, InvoiceLineForm
from apps.invoicing.models import BusinessInvoiceSettings
from apps.invoicing.selectors import filter_invoices, invoices_for
from apps.invoicing.services import InvoiceError, create_manual_invoice, get_invoice_settings


@pytest.mark.django_db
def test_manual_invoice_service_rejects_another_business_product():
    seller = make_business(name="فاکتور مالک", owner_phone="09120002201")
    other = make_business(name="فاکتور دیگر", owner_phone="09120002202")
    foreign = make_item(other, lot_code="INV-FOREIGN")

    with pytest.raises(InvoiceError, match="متعلق"):
        create_manual_invoice(
            business=seller,
            membership=owner_membership(seller),
            customer_name="مشتری",
            lines=[
                {
                    "item": foreign,
                    "quantity": Decimal("1"),
                    "unit_price": Decimal("1000"),
                }
            ],
        )


@pytest.mark.django_db
def test_invoice_form_uses_lazy_product_picker(client):
    seller = make_business(name="فاکتور فرم", owner_phone="09120002203")
    make_item(seller, lot_code="INV-PICK")
    membership = owner_membership(seller)
    client.force_login(membership.user)
    session = client.session
    session["current_business_id"] = str(seller.id)
    session.save()

    body = client.get(reverse("invoicing:create")).content.decode()

    assert "data-product-picker" in body
    assert 'type="search"' in body
    assert 'name="lines-0-product_name"' in body
    assert "یا نام یک سنگ دلخواه را مستقیم بنویسید" in body
    assert "INV-PICK" not in body
    assert "data-step-target" not in body
    assert "مرحله بعد" not in body
    assert "قبلی" not in body
    assert "ظاهر و ذخیره" not in body
    assert "نوع سنگ</span>" not in body
    assert "نوع تخفیف</span>" not in body
    assert "مقدار تخفیف</span>" not in body
    assert "data-counterparty-panel" in body
    assert "data-local-new-fields" in body
    assert "data-preview-toggle" in body
    assert '<fieldset class="invoice-counterparty-fieldset"' in body
    assert 'id="invoice-customer-fields"' in body
    assert 'aria-controls="invoice-local-new-fields"' in body
    assert 'id="invoice-local-new-fields"' in body
    assert 'id="invoice-customer-paid"' in body
    assert "data-mode-hint" in body
    assert "data-invoice-primary-action" in body
    assert 'data-preview-expand aria-expanded="false" aria-controls="invoice-preview-canvas"' in body
    assert "صدور نهایی" in body
    assert re.search(r"css/invoice\.css\?v=\d+", body)
    assert "js/invoice_calculator.js?v=4" in body
    assert re.search(r"js/invoice_editor\.js\?v=\d+", body)

    editor_script = (settings.BASE_DIR / "static/js/invoice_editor.js").read_text(encoding="utf-8")
    assert "response.status === 204" in editor_script
    assert "result.pending" in editor_script


@pytest.mark.django_db
def test_invoice_settings_present_color_as_one_control(client):
    seller = make_business(name="تنظیم رنگ فاکتور", owner_phone="09120002204")
    membership = owner_membership(seller)
    client.force_login(membership.user)
    session = client.session
    session["current_business_id"] = str(seller.id)
    session.save()

    body = client.get(reverse("invoicing:settings")).content.decode()

    assert "data-invoice-color-picker" in body
    assert "رنگ‌بندی فاکتور" in body
    assert "پالت رنگ</span>" not in body
    assert "رنگ اصلی</span>" not in body
    assert body.count('id="id_bank_information"') == 1
    assert body.count('id="id_logo"') == 1
    assert body.count('id="id_stamp"') == 1
    assert body.count('id="id_signature"') == 1
    assert 'id="id_personal-image"' in body
    assert 'id="id_personal-remove_signature"' in body
    assert "ارز پیش‌فرض" in body
    assert "واحد نمایش پیش‌فرض" in body


@pytest.mark.django_db
def test_invoice_numeric_controls_accept_persian_digits(client):
    seller = make_business(name="فاکتور اعداد فارسی", owner_phone="09120002206")
    membership = owner_membership(seller)
    client.force_login(membership.user)
    session = client.session
    session["current_business_id"] = str(seller.id)
    session.save()

    body = client.get(reverse("invoicing:create")).content.decode()

    for name in ("lines-0-quantity", "lines-0-unit_price", "tax_amount"):
        control = re.search(rf'<input[^>]*name="{name}"[^>]*>', body)
        assert control is not None
        assert 'type="text"' in control.group()
        assert 'inputmode="decimal"' in control.group()


@pytest.mark.django_db
def test_invoice_list_search_normalizes_persian_numbers_and_sorts_dates():
    seller = make_business(name="فاکتور مرتب", owner_phone="09120002207")
    membership = owner_membership(seller)
    common = {
        "business": seller,
        "membership": membership,
        "lines": [{"product_name": "سنگ", "quantity": 1, "unit_price": 100}],
        "paid_amount": 100,
        "issue": True,
    }
    older = create_manual_invoice(customer_name="قدیمی", issue_date=date(2026, 1, 1), **common)
    newer = create_manual_invoice(customer_name="جدید", issue_date=date(2026, 2, 1), **common)

    assert list(filter_invoices(invoices_for(seller), business=seller, q="۰۰۰۰۱")) == [older]
    assert list(filter_invoices(invoices_for(seller), business=seller, sort="oldest")) == [older, newer]
    assert list(filter_invoices(invoices_for(seller), business=seller, sort="newest")) == [newer, older]


def test_cheque_forms_have_unique_control_ids_and_keep_the_current_status():
    first = ChequeStatusForm(prefix="cheque-first", initial={"status": "bounced"})
    second = ChequeStatusForm(prefix="cheque-second", initial={"status": "received"})

    assert first["status"].id_for_label != second["status"].id_for_label
    assert first["status"].value() == "bounced"


@pytest.mark.django_db
def test_counterparty_links_explains_the_empty_next_step(client):
    seller = make_business(name="فاکتور اتصال", owner_phone="09120002208")
    membership = owner_membership(seller)
    client.force_login(membership.user)
    session = client.session
    session["current_business_id"] = str(seller.id)
    session.save()

    body = client.get(reverse("invoicing:counterparty_links")).content.decode()

    assert 'class="panel stack-md"' in body
    assert "همکار محلیِ بدون اتصال ندارید" in body
    assert "ساخت فاکتور همکار محلی" in body
    assert 'class="card' not in body


@pytest.mark.django_db
def test_invoice_palette_persists_its_canonical_primary_color():
    business = make_business(name="رنگ‌بندی یکپارچه", owner_phone="09120002205")
    settings_row = get_invoice_settings(business)
    form = BusinessInvoiceSettingsForm(
        {
            "palette": BusinessInvoiceSettings.Palette.OCEAN,
            "primary_color": "#1f513c",
            "header_style": BusinessInvoiceSettings.HeaderStyle.MODERN,
            "logo_size": BusinessInvoiceSettings.LogoSize.MEDIUM,
            "show_bank_information": "on",
            "show_stamp": "on",
            "show_signature": "on",
            "default_currency": "IRR",
            "default_display_unit": "IRR",
        },
        instance=settings_row,
    )

    assert form.is_valid(), form.errors
    saved = form.save()
    assert saved.palette == BusinessInvoiceSettings.Palette.OCEAN
    assert saved.primary_color == "#164e78"


def test_invalid_product_identifier_becomes_a_field_error():
    form = InvoiceLineForm(
        {
            "item": "not-a-uuid",
            "quantity": "1",
            "unit_price": "1000",
        }
    )

    assert not form.is_valid()
    assert "item" in form.errors
