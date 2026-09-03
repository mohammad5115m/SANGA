from __future__ import annotations

import uuid
from decimal import Decimal
from io import BytesIO
from zipfile import ZipFile

import pymupdf
import pytest
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.core.testing import make_business, make_item, owner_membership
from apps.invoicing.documents import build_invoice_document
from apps.invoicing.forms import BusinessInvoiceSettingsForm
from apps.invoicing.models import BusinessInvoiceSettings, SalesInvoice
from apps.invoicing.rendering import document_html, render_pdf, render_png
from apps.invoicing.services import (
    InvoiceError,
    create_manual_invoice,
    duplicate_invoice,
    get_invoice_settings,
    issue_invoice,
    save_as_template,
)
from apps.invoicing.uploads import sanitize_invoice_image
from apps.trading.services import record_direct_sale


@pytest.fixture
def seller(db):
    business = make_business(name="سنگ اسناد", owner_phone="09121117701")
    return business, owner_membership(business)


def _lines(count=1):
    return [
        {
            "product_name": f"تراورتن ردیف {index + 1}",
            "quantity": Decimal("2"),
            "unit_price": Decimal("1000"),
            "discount_type": "percent",
            "discount_value": Decimal("10"),
            "item": None,
        }
        for index in range(count)
    ]


def _invoice(seller, *, issue=False, count=1, **kwargs):
    business, membership = seller
    values = {
        "business": business,
        "membership": membership,
        "lines": _lines(count),
        "customer_name": "خریدار نمونه",
        "invoice_discount_type": "amount",
        "invoice_discount_value": Decimal("100"),
        "tax_amount": Decimal("50"),
        "shipping_amount": Decimal("20"),
        "adjustment_amount": Decimal("5"),
        "paid_amount": Decimal("200"),
        "issue": issue,
    }
    if issue and "paid_amount" not in kwargs:
        values["paid_amount"] = Decimal("1800") * count - Decimal("25")
    values.update(kwargs)
    return create_manual_invoice(**values)


def _login(client, business):
    membership = owner_membership(business)
    client.force_login(membership.user)
    session = client.session
    session["current_business_id"] = str(business.id)
    session.save()


def _form_payload(*, action="draft"):
    payload = {
        "customer_name": "خریدار فرم",
        "customer_phone": "09120000000",
        "buyer_address": "تهران",
        "issue_date": timezone.localdate().isoformat(),
        "currency": "IRR",
        "display_unit": "IRR",
        "invoice_discount_type": "none",
        "invoice_discount_value": "0",
        "tax_amount": "0",
        "shipping_amount": "0",
        "adjustment_amount": "0",
        "paid_amount": "0",
        "notes": "",
        "payment_terms": "",
        "palette": "forest",
        "primary_color": "#1f513c",
        "header_style": "modern",
        "logo_size": "medium",
        "show_bank_information": "on",
        "show_stamp": "on",
        "show_signature": "on",
        "action": action,
        "lines-TOTAL_FORMS": "2",
        "lines-INITIAL_FORMS": "0",
        "lines-MIN_NUM_FORMS": "0",
        "lines-MAX_NUM_FORMS": "100",
    }
    for index, name in enumerate(("سنگ ردیف اول", "سنگ ردیف دوم")):
        payload.update(
            {
                f"lines-{index}-item": "",
                f"lines-{index}-product_name": name,
                f"lines-{index}-stone_type": "تراورتن",
                f"lines-{index}-grade": "سوپر",
                f"lines-{index}-description": "",
                f"lines-{index}-quantity": "2",
                f"lines-{index}-unit": "متر مربع",
                f"lines-{index}-unit_price": "1000",
                f"lines-{index}-discount_type": "none",
                f"lines-{index}-discount_value": "0",
                f"lines-{index}-ORDER": str(index),
            }
        )
    return payload


@pytest.mark.django_db
def test_draft_has_no_number_and_issue_freezes_server_calculated_totals(seller):
    invoice = _invoice(seller)

    assert invoice.status == SalesInvoice.Status.DRAFT
    assert invoice.number == ""
    assert invoice.gross_subtotal == Decimal("2000.00")
    assert invoice.line_discount_total == Decimal("200.00")
    assert invoice.total_amount == Decimal("1775.00")
    assert invoice.amount_due == Decimal("1575.00")

    with pytest.raises(InvoiceError, match="دریافت کامل"):
        issue_invoice(invoice=invoice, membership=seller[1])

    invoice.paid_amount = invoice.total_amount
    invoice.amount_due = Decimal("0")
    invoice.payment_status = SalesInvoice.PaymentStatus.PAID
    invoice.save(update_fields=["paid_amount", "amount_due", "payment_status", "updated_at"])
    issued = issue_invoice(invoice=invoice, membership=seller[1])
    assert issued.status == SalesInvoice.Status.ISSUED
    assert issued.number == "00001"

    issued.notes = "تلاش برای بازنویسی تاریخچه"
    with pytest.raises(ValidationError, match="تغییرناپذیر"):
        issued.save()

    line = issued.items.get()
    line.quantity = Decimal("99")
    with pytest.raises(ValidationError, match="قابل تغییر نیست"):
        line.save()


@pytest.mark.django_db
def test_create_and_edit_views_add_delete_reorder_and_issue_lines(client, seller):
    _login(client, seller[0])
    created = client.post(reverse("invoicing:create"), _form_payload())
    invoice = SalesInvoice.objects.get(customer_name="خریدار فرم")

    assert created.status_code == 302
    assert invoice.status == SalesInvoice.Status.DRAFT
    assert list(invoice.items.values_list("product_name", flat=True)) == [
        "سنگ ردیف اول",
        "سنگ ردیف دوم",
    ]

    edited_payload = _form_payload(action="issue")
    edited_payload["lines-0-DELETE"] = "on"
    edited_payload["lines-1-product_name"] = "سنگ ویرایش‌شده"
    edited_payload["lines-1-quantity"] = "3"
    edited_payload["lines-1-ORDER"] = "0"
    edited_payload["paid_amount"] = "3000"
    edited = client.post(
        reverse("invoicing:edit", kwargs={"invoice_id": invoice.id}),
        edited_payload,
    )
    invoice.refresh_from_db()

    assert edited.status_code == 302
    assert invoice.status == SalesInvoice.Status.ISSUED
    assert invoice.items.count() == 1
    assert invoice.items.get().product_name == "سنگ ویرایش‌شده"
    assert invoice.items.get().quantity == Decimal("3.000")


@pytest.mark.django_db
def test_failed_partner_send_redirects_to_the_recoverable_draft(client, seller):
    _login(client, seller[0])
    buyer = make_business(name="خریدار همکار", owner_phone="09121117702")
    payload = _form_payload(action="issue")
    payload.update(
        {
            "submission_id": str(uuid.uuid4()),
            "counterparty_mode": SalesInvoice.Counterparty.BUSINESS,
            "buyer_business": str(buyer.id),
            "settlement_method": SalesInvoice.SettlementMethod.CREDIT,
            "cash_amount": "0",
            "credit_amount": "0",
            "cheque_amount": "0",
        }
    )

    response = client.post(reverse("invoicing:create"), payload)
    invoice = SalesInvoice.objects.get(buyer_business=buyer)

    assert response.status_code == 302
    assert response.url == reverse("invoicing:edit", kwargs={"invoice_id": invoice.id})
    assert invoice.status == SalesInvoice.Status.DRAFT


@pytest.mark.django_db
def test_duplicate_is_a_new_draft_and_resets_payment_state(seller):
    invoice = _invoice(seller, issue=True)
    duplicate = duplicate_invoice(invoice=invoice, membership=seller[1])

    assert duplicate.id != invoice.id
    assert duplicate.status == SalesInvoice.Status.DRAFT
    assert duplicate.number == ""
    assert duplicate.paid_amount == Decimal("0.00")
    assert duplicate.amount_due == duplicate.total_amount
    assert duplicate.items.count() == invoice.items.count()


@pytest.mark.django_db
def test_template_reuses_content_but_not_document_identity_or_payment(seller):
    invoice = _invoice(seller, issue=True)
    template = save_as_template(invoice=invoice, name="فروش متداول", membership=seller[1])

    assert template.payload["customer_name"] == invoice.customer_name
    assert "number" not in template.payload
    assert "status" not in template.payload
    assert "paid_amount" not in template.payload


@pytest.mark.django_db
def test_issued_invoice_keeps_seller_branding_snapshot(seller):
    settings_row = BusinessInvoiceSettings.objects.create(
        business=seller[0], legal_name="نام رسمی هنگام صدور"
    )
    invoice = _invoice(seller, issue=True)
    settings_row.legal_name = "نام رسمی جدید"
    settings_row.save(update_fields=["legal_name", "updated_at"])

    document = build_invoice_document(invoice)
    assert document["seller"]["name"] == "نام رسمی هنگام صدور"
    assert "نام رسمی هنگام صدور" in document_html(invoice)
    assert "نام رسمی جدید" not in document_html(invoice)


@pytest.mark.django_db
def test_colleague_previous_balance_is_snapshotted_but_not_silently_added(seller):
    buyer = make_business(name="سنگ خریدار مانده", owner_phone="09121117704")
    item = make_item(seller[0], lot_code="BALANCE-1")
    first_trade = record_direct_sale(
        seller_business=seller[0],
        membership=seller[1],
        item=item,
        quantity_sqm=Decimal("2"),
        unit_price=Decimal("1000"),
        buyer_business=buyer,
    )
    second_trade = record_direct_sale(
        seller_business=seller[0],
        membership=seller[1],
        item=item,
        quantity_sqm=Decimal("3"),
        unit_price=Decimal("1000"),
        buyer_business=buyer,
    )

    first = SalesInvoice.objects.get(trade=first_trade)
    second = SalesInvoice.objects.get(trade=second_trade)
    assert first.previous_balance_snapshot == Decimal("0.00")
    assert second.previous_balance_snapshot == first.total_amount
    assert second.previous_balance_included is False
    assert second.amount_due == second.total_amount
    assert build_invoice_document(second)["previous_balance_included"] is False


@pytest.mark.django_db
def test_pdf_keeps_text_selectable_and_png_comes_from_the_same_document(seller):
    invoice = _invoice(seller, issue=True)
    invoice.refresh_from_db()
    updated_at = invoice.updated_at
    pdf = render_pdf(invoice)
    repeated_pdf = render_pdf(invoice)
    invoice.refresh_from_db()
    assert invoice.updated_at == updated_at

    parsed = pymupdf.open(stream=pdf, filetype="pdf")
    try:
        extracted = "".join(page.get_text() for page in parsed)
    finally:
        parsed.close()
    repeated = pymupdf.open(stream=repeated_pdf, filetype="pdf")
    try:
        repeated_text = "".join(page.get_text() for page in repeated)
    finally:
        repeated.close()

    assert pdf.startswith(b"%PDF")
    assert invoice.number in extracted
    assert invoice.number in repeated_text

    image, content_type, extension = render_png(invoice)
    assert content_type == "image/png"
    assert extension == "png"
    assert image.startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.django_db
def test_long_invoice_paginates_without_rasterizing_the_pdf(seller):
    invoice = _invoice(
        seller,
        issue=True,
        count=75,
        invoice_discount_type="none",
        invoice_discount_value=0,
        paid_amount=Decimal("135075"),
    )
    pdf = render_pdf(invoice)
    parsed = pymupdf.open(stream=pdf, filetype="pdf")
    try:
        assert parsed.page_count >= 2
        page_count = parsed.page_count
        assert all(page.get_text().strip() for page in parsed)
    finally:
        parsed.close()
    content, content_type, extension = render_png(invoice)
    assert content_type == "application/zip"
    assert extension == "zip"
    with ZipFile(BytesIO(content)) as archive:
        assert len(archive.namelist()) == page_count
        assert all(name.endswith(".png") for name in archive.namelist())


def test_upload_is_verified_by_content_and_reencoded_as_png():
    source = BytesIO()
    Image.new("RGB", (32, 24), "#1f513c").save(source, format="JPEG", comment=b"metadata")
    upload = SimpleUploadedFile("brand.jpg", source.getvalue(), content_type="image/jpeg")

    cleaned = sanitize_invoice_image(upload, stem="../../Logo")
    assert cleaned.name == "logo.png"
    assert cleaned.read().startswith(b"\x89PNG\r\n\x1a\n")


def test_disguised_non_image_upload_is_rejected():
    upload = SimpleUploadedFile("logo.jpg", b"<script>alert(1)</script>", content_type="image/jpeg")
    with pytest.raises(ValidationError, match="تصویر سالم"):
        sanitize_invoice_image(upload)


@pytest.mark.django_db
def test_service_limits_invoice_size(seller):
    with pytest.raises(InvoiceError, match="۱۰۰"):
        _invoice(seller, count=101)


@pytest.mark.django_db
def test_other_tenant_cannot_load_a_saved_template(client, seller):
    invoice = _invoice(seller, issue=True)
    template = save_as_template(invoice=invoice, name="قالب خصوصی", membership=seller[1])
    other = make_business(name="سنگ دیگر", owner_phone="09121117702")
    _login(client, other)

    response = client.get(reverse("invoicing:create"), {"template": template.id})
    body = response.content.decode()
    assert response.status_code == 200
    assert "خریدار نمونه" not in body
    assert "قالب خصوصی" not in body


@pytest.mark.django_db
def test_invoice_list_uses_the_selected_display_unit(client, seller):
    invoice = _invoice(seller, issue=True, currency="IRR", display_unit="IRT")
    _login(client, seller[0])

    body = client.get(reverse("invoicing:list")).content.decode()

    assert f"{invoice.display_total_amount:,.0f}" in body
    assert "تومان" in body


@pytest.mark.django_db
@override_settings(INVOICE_EXPORTS_PER_MINUTE=1)
def test_export_endpoint_is_rate_limited(client, seller, monkeypatch):
    invoice = _invoice(seller, issue=True)
    _login(client, seller[0])
    cache.clear()
    monkeypatch.setattr("apps.invoicing.views.render_pdf", lambda _invoice: b"%PDF-test")

    first = client.get(reverse("invoicing:pdf", kwargs={"invoice_id": invoice.id}))
    second = client.get(reverse("invoicing:pdf", kwargs={"invoice_id": invoice.id}))

    assert first.status_code == 200
    assert first["Content-Type"] == "application/pdf"
    assert "attachment" in first["Content-Disposition"]
    assert f"sanga-invoice-{invoice.number}.pdf" in first["Content-Disposition"]
    assert first["X-Content-Type-Options"] == "nosniff"
    assert second.status_code == 429


@pytest.mark.django_db(transaction=True)
def test_brand_asset_endpoint_is_tenant_scoped(client, seller, tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path):
        source = BytesIO()
        Image.new("RGB", (16, 16), "#1f513c").save(source, format="PNG")
        upload = SimpleUploadedFile("logo.png", source.getvalue(), content_type="image/png")
        settings_row = get_invoice_settings(seller[0])
        settings_row.logo = sanitize_invoice_image(upload, stem="logo")
        settings_row.save(update_fields=["logo", "updated_at"])

        _login(client, seller[0])
        own = client.get(reverse("invoicing:asset", kwargs={"kind": "logo"}))
        assert own.status_code == 200
        own.close()
        assert client.get(settings_row.logo.url).status_code == 404

        other = make_business(name="سنگ رقیب", owner_phone="09121117703")
        _login(client, other)
        denied = client.get(reverse("invoicing:asset", kwargs={"kind": "logo"}))
        assert denied.status_code == 404


@pytest.mark.django_db
def test_document_rejects_a_buyer_signature_path_from_another_tenant(seller):
    invoice = _invoice(seller, issue=True)
    other = make_business(name="سنگ امضای دیگر", owner_phone="09121117705")
    invoice.buyer_signature.name = f"invoice-assets/{other.id}/signature.png"

    assert build_invoice_document(invoice)["buyer_signature"] == ""


@pytest.mark.django_db
def test_brand_asset_can_be_removed_without_exposing_its_storage_url(seller, tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path):
        source = BytesIO()
        Image.new("RGB", (16, 16), "#1f513c").save(source, format="PNG")
        upload = SimpleUploadedFile("logo.png", source.getvalue(), content_type="image/png")
        settings_row = get_invoice_settings(seller[0])
        settings_row.logo = sanitize_invoice_image(upload, stem="logo")
        settings_row.save(update_fields=["logo", "updated_at"])

        form = BusinessInvoiceSettingsForm(
            {
                "palette": "forest",
                "primary_color": "#1f513c",
                "header_style": "modern",
                "logo_size": "medium",
                "default_currency": "IRR",
                "default_display_unit": "IRR",
                "remove_logo": "on",
            },
            instance=settings_row,
        )
        assert form.is_valid(), form.errors
        saved = form.save()
        assert not saved.logo
        assert "href=" not in str(form["logo"])
