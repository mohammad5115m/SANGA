"""Canonical invoice document context shared by screen, print, PDF and image."""

from __future__ import annotations

import base64
import logging
from decimal import Decimal

from django.core.files.storage import default_storage

from .calculations import amount_to_words, to_display_amount
from .models import BusinessInvoiceSettings, SalesInvoice
from .services import appearance_snapshot, seller_snapshot

logger = logging.getLogger(__name__)

DISPLAY_LABELS = {"IRR": "ریال", "IRT": "تومان", "EUR": "یورو", "USD": "دلار"}
PALETTE_COLORS = {
    "forest": "#1f513c",
    "ocean": "#164e78",
    "charcoal": "#30343b",
    "saffron": "#7a4700",
}


def format_number(value: Decimal, *, decimal_places: int = 2) -> str:
    rendered = f"{Decimal(value):,.{decimal_places}f}"
    return rendered.rstrip("0").rstrip(".")


def asset_data_uri(name: str, *, business_id) -> str:
    if not name:
        return ""
    allowed_prefix = f"invoice-assets/{business_id}/"
    if not name.startswith(allowed_prefix) or ".." in name:
        logger.warning("Rejected invoice asset outside its tenant prefix")
        return ""
    try:
        if not default_storage.exists(name) or default_storage.size(name) > 5 * 1024 * 1024:
            return ""
        with default_storage.open(name, "rb") as handle:
            encoded = base64.b64encode(handle.read()).decode("ascii")
    except Exception:
        logger.warning("Invoice asset %s could not be read", name, exc_info=True)
        return ""
    return f"data:image/png;base64,{encoded}"


def uploaded_data_uri(upload) -> str:
    if not upload:
        return ""
    try:
        position = upload.tell()
        upload.seek(0)
        content = upload.read(5 * 1024 * 1024 + 1)
        upload.seek(position)
    except (AttributeError, OSError, ValueError):
        return ""
    if not content or len(content) > 5 * 1024 * 1024:
        return ""
    return f"data:image/png;base64,{base64.b64encode(content).decode('ascii')}"


def _settings_for_document(business) -> BusinessInvoiceSettings:
    return (
        BusinessInvoiceSettings.objects.filter(business=business).first()
        or BusinessInvoiceSettings(business=business)
    )


def _theme(invoice: SalesInvoice) -> dict:
    settings_row = _settings_for_document(invoice.seller_business)
    theme = appearance_snapshot(settings_row, invoice.appearance_snapshot)
    if theme.get("palette") != "custom":
        theme["primary_color"] = PALETTE_COLORS.get(
            theme.get("palette"), theme.get("primary_color", "#1f513c")
        )
    theme["primary_color"] = theme.get("primary_color", "#1f513c")
    business_id = invoice.seller_business_id
    theme["logo"] = asset_data_uri(theme.get("logo_name", ""), business_id=business_id)
    theme["stamp"] = asset_data_uri(theme.get("stamp_name", ""), business_id=business_id)
    theme["signature"] = asset_data_uri(
        theme.get("signature_name", ""), business_id=business_id
    )
    return theme


def _money(invoice: SalesInvoice, value) -> Decimal:
    return to_display_amount(
        value, currency=invoice.currency, display_unit=invoice.display_unit
    )


def build_invoice_document(invoice: SalesInvoice) -> dict:
    settings_row = _settings_for_document(invoice.seller_business)
    seller = invoice.seller_snapshot or seller_snapshot(invoice.seller_business, settings_row)
    unit_label = DISPLAY_LABELS.get(invoice.display_unit, invoice.display_unit)
    lines = []
    for index, line in enumerate(invoice.items.all(), start=1):
        lines.append(
            {
                "index": index,
                "product_name": line.product_name,
                "stone_type": line.stone_type,
                "grade": line.grade,
                "description": line.description,
                "quantity": format_number(line.quantity, decimal_places=3),
                "unit": line.unit,
                "unit_price": format_number(_money(invoice, line.unit_price)),
                "gross_total": format_number(_money(invoice, line.gross_total)),
                "discount_amount": format_number(_money(invoice, line.discount_amount)),
                "line_total": format_number(_money(invoice, line.line_total)),
                "has_discount": bool(line.discount_amount),
            }
        )
    due = _money(invoice, invoice.amount_due)
    same_balance_currency = invoice.previous_balance_currency == invoice.currency
    previous_balance = (
        _money(invoice, invoice.previous_balance_snapshot)
        if same_balance_currency
        else invoice.previous_balance_snapshot
    )
    return {
        "id": str(invoice.id),
        "number": invoice.display_number,
        "issue_date": invoice.issue_date,
        "status": invoice.status,
        "status_label": invoice.get_status_display(),
        "cancel_reason": invoice.cancel_reason,
        "payment_status_label": invoice.get_payment_status_display(),
        "seller": seller,
        "buyer": {
            "name": invoice.buyer_name,
            "phone": invoice.buyer_phone or invoice.customer_phone,
            "address": invoice.buyer_address,
        },
        "lines": lines,
        "currency": invoice.currency,
        "display_unit": invoice.display_unit,
        "unit_label": unit_label,
        "gross_subtotal": format_number(_money(invoice, invoice.gross_subtotal)),
        "line_discount_total": format_number(_money(invoice, invoice.line_discount_total)),
        "net_items_total": format_number(_money(invoice, invoice.net_items_total)),
        "invoice_discount_amount": format_number(_money(invoice, invoice.invoice_discount_amount)),
        "tax_amount": format_number(_money(invoice, invoice.tax_amount)),
        "shipping_amount": format_number(_money(invoice, invoice.shipping_amount)),
        "adjustment_amount": format_number(_money(invoice, invoice.adjustment_amount)),
        "total_amount": format_number(_money(invoice, invoice.total_amount)),
        "paid_amount": format_number(_money(invoice, invoice.paid_amount)),
        "previous_balance": format_number(previous_balance),
        "previous_balance_unit_label": (
            unit_label
            if same_balance_currency
            else DISPLAY_LABELS.get(
                invoice.previous_balance_currency, invoice.previous_balance_currency
            )
        ),
        "previous_balance_state": invoice.get_previous_balance_state_display(),
        "previous_balance_included": invoice.previous_balance_included,
        "amount_due": format_number(due),
        "amount_due_words": amount_to_words(due),
        "notes": invoice.notes,
        "payment_terms": invoice.payment_terms,
        "theme": _theme(invoice),
        "buyer_signature": asset_data_uri(
            invoice.buyer_signature.name if invoice.buyer_signature else "",
            business_id=invoice.seller_business_id,
        ),
        "has_line_discounts": bool(invoice.line_discount_total),
        "has_invoice_discount": bool(invoice.invoice_discount_amount),
        "has_tax": bool(invoice.tax_amount),
        "has_shipping": bool(invoice.shipping_amount),
        "has_adjustment": bool(invoice.adjustment_amount),
        "has_paid": bool(invoice.paid_amount),
        "has_previous_balance": bool(invoice.previous_balance_snapshot),
    }


def build_preview_document(*, business, header: dict, calculated, totals) -> dict:
    """Canonical document context for a valid, unsaved form submission."""
    settings_row = _settings_for_document(business)
    currency = header["currency"]
    display_unit = header["display_unit"]

    def display(value):
        return to_display_amount(value, currency=currency, display_unit=display_unit)

    theme = appearance_snapshot(settings_row, header.get("appearance"))
    if theme.get("palette") != "custom":
        theme["primary_color"] = PALETTE_COLORS.get(
            theme.get("palette"), theme.get("primary_color", "#1f513c")
        )
    theme["logo"] = asset_data_uri(
        theme.get("logo_name", ""), business_id=business.id
    )
    theme["stamp"] = asset_data_uri(
        theme.get("stamp_name", ""), business_id=business.id
    )
    theme["signature"] = asset_data_uri(
        theme.get("signature_name", ""), business_id=business.id
    )
    lines = []
    for index, line in enumerate(calculated, start=1):
        lines.append(
            {
                "index": index,
                "product_name": line.source["product_name"],
                "stone_type": line.source.get("stone_type", ""),
                "grade": line.source.get("grade", ""),
                "description": line.source.get("description", ""),
                "quantity": format_number(line.quantity, decimal_places=3),
                "unit": line.source.get("unit", "متر مربع"),
                "unit_price": format_number(display(line.unit_price)),
                "gross_total": format_number(display(line.gross_total)),
                "discount_amount": format_number(display(line.discount_amount)),
                "line_total": format_number(display(line.line_total)),
                "has_discount": bool(line.discount_amount),
            }
        )
    due = display(totals.amount_due)
    mode = header.get("counterparty_mode", SalesInvoice.Counterparty.CUSTOMER)
    if mode == SalesInvoice.Counterparty.BUSINESS and header.get("buyer_business"):
        counterparty = header["buyer_business"]
        buyer = {
            "name": counterparty.name,
            "phone": counterparty.phone,
            "address": counterparty.address,
        }
    elif mode == SalesInvoice.Counterparty.LOCAL:
        counterparty = header.get("local_counterparty")
        buyer = {
            "name": counterparty.name if counterparty else header.get("local_name", ""),
            "phone": counterparty.phone if counterparty else header.get("local_phone", ""),
            "address": counterparty.address if counterparty else header.get("local_address", ""),
        }
    else:
        buyer = {
            "name": header.get("customer_name", ""),
            "phone": header.get("customer_phone", ""),
            "address": header.get("buyer_address", ""),
        }
    return {
        "id": "preview",
        "number": "پیش‌نویس",
        "issue_date": header["issue_date"],
        "status": "draft",
        "status_label": "پیش‌نمایش",
        "cancel_reason": "",
        "payment_status_label": "پیش‌نویس",
        "seller": seller_snapshot(business, settings_row),
        "buyer": buyer,
        "lines": lines,
        "currency": currency,
        "display_unit": display_unit,
        "unit_label": DISPLAY_LABELS.get(display_unit, display_unit),
        "gross_subtotal": format_number(display(totals.gross_subtotal)),
        "line_discount_total": format_number(display(totals.line_discount_total)),
        "net_items_total": format_number(display(totals.net_items_total)),
        "invoice_discount_amount": format_number(display(totals.invoice_discount_amount)),
        "tax_amount": format_number(display(totals.tax_amount)),
        "shipping_amount": format_number(display(totals.shipping_amount)),
        "adjustment_amount": format_number(display(totals.adjustment_amount)),
        "total_amount": format_number(display(totals.total_amount)),
        "paid_amount": format_number(display(totals.paid_amount)),
        "previous_balance": "0",
        "previous_balance_unit_label": DISPLAY_LABELS.get(display_unit, display_unit),
        "previous_balance_state": "تسویه",
        "previous_balance_included": False,
        "amount_due": format_number(due),
        "amount_due_words": amount_to_words(due),
        "notes": header.get("notes", ""),
        "payment_terms": header.get("payment_terms", ""),
        "theme": theme,
        "buyer_signature": uploaded_data_uri(header.get("buyer_signature")),
        "has_line_discounts": bool(totals.line_discount_total),
        "has_invoice_discount": bool(totals.invoice_discount_amount),
        "has_tax": bool(totals.tax_amount),
        "has_shipping": bool(totals.shipping_amount),
        "has_adjustment": bool(totals.adjustment_amount),
        "has_paid": bool(totals.paid_amount),
        "has_previous_balance": False,
    }
