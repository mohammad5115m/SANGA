"""Invoice commands: one calculator, immutable issue snapshots, no ledger writes."""

from __future__ import annotations

import logging
import re
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from apps.businesses.entitlements import ISSUE_INVOICES, EntitlementError, require_entitlement
from apps.businesses.models import Business, BusinessMembership
from apps.businesses.permissions import INVOICE_MANAGE, TRADE_CONFIRM

from .calculations import (
    DISCOUNT_AMOUNT,
    DISCOUNT_NONE,
    InvoiceCalculationError,
    calculate_invoice,
    to_storage_amount,
    validate_display_unit,
)
from .models import (
    BusinessInvoiceSettings,
    InvoiceTemplate,
    SalesInvoice,
    SalesInvoiceItem,
)

logger = logging.getLogger(__name__)
ALLOWED_UNITS = {"متر مربع", "عدد", "متر", "تن", "کیلوگرم", "پالت"}
PALETTES = {choice for choice, _label in BusinessInvoiceSettings.Palette.choices}
HEADER_STYLES = {choice for choice, _label in BusinessInvoiceSettings.HeaderStyle.choices}
LOGO_SIZES = {choice for choice, _label in BusinessInvoiceSettings.LogoSize.choices}


class InvoiceError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def _readable_on_white(color: str) -> bool:
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        return False
    channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        channel / 12.92
        if channel <= 0.04045
        else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    luminance = 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
    return 1.05 / (luminance + 0.05) >= 4.5


def _require_manage(business: Business, membership: BusinessMembership) -> None:
    if membership is None or not membership.has_capability(INVOICE_MANAGE):
        raise InvoiceError("اجازه مدیریت فاکتور را ندارید.")
    _require_seller_may_invoice(business, membership)


def _require_seller_may_invoice(business: Business, membership: BusinessMembership) -> None:
    if membership is None or membership.business_id != business.id:
        raise InvoiceError("دسترسی نامعتبر است.")
    try:
        require_entitlement(business, ISSUE_INVOICES)
    except EntitlementError as exc:
        raise InvoiceError(exc.message) from exc


def get_invoice_settings(business: Business) -> BusinessInvoiceSettings:
    settings_row, _ = BusinessInvoiceSettings.objects.get_or_create(business=business)
    return settings_row


def seller_snapshot(business: Business, settings_row: BusinessInvoiceSettings) -> dict:
    return {
        "name": settings_row.legal_name or business.name,
        "phone": business.phone,
        "address": business.address,
        "city": business.city,
        "province": business.province,
        "tax_id": settings_row.tax_id,
        "bank_information": settings_row.bank_information,
    }


def appearance_snapshot(
    settings_row: BusinessInvoiceSettings, overrides: dict | None = None
) -> dict:
    asset_prefix = f"invoice-assets/{settings_row.business_id}/"

    def safe_asset(field) -> str:
        value = field.name if field else ""
        return value if value.startswith(asset_prefix) and ".." not in value else ""

    configured_color = str(settings_row.primary_color or "")
    result = {
        "palette": settings_row.palette if settings_row.palette in PALETTES else "forest",
        "primary_color": (
            configured_color.lower()
            if _readable_on_white(configured_color)
            else "#1f513c"
        ),
        "header_style": (
            settings_row.header_style if settings_row.header_style in HEADER_STYLES else "modern"
        ),
        "logo_size": settings_row.logo_size if settings_row.logo_size in LOGO_SIZES else "medium",
        "show_bank_information": bool(settings_row.show_bank_information),
        "show_stamp": bool(settings_row.show_stamp),
        "show_signature": bool(settings_row.show_signature),
        "logo_name": safe_asset(settings_row.logo),
        "stamp_name": safe_asset(settings_row.stamp),
        "signature_name": safe_asset(settings_row.signature),
    }
    provided = overrides or {}
    if provided.get("palette") in PALETTES:
        result["palette"] = provided["palette"]
    if provided.get("header_style") in HEADER_STYLES:
        result["header_style"] = provided["header_style"]
    if provided.get("logo_size") in LOGO_SIZES:
        result["logo_size"] = provided["logo_size"]
    provided_color = str(provided.get("primary_color", ""))
    if _readable_on_white(provided_color):
        result["primary_color"] = provided_color.lower()
    for key in ("show_bank_information", "show_stamp", "show_signature"):
        if isinstance(provided.get(key), bool):
            result[key] = provided[key]
    for key in ("logo_name", "stamp_name", "signature_name"):
        value = str(provided.get(key, ""))
        if value.startswith(asset_prefix) and ".." not in value:
            result[key] = value
    return result


def allocate_number(business: Business) -> str:
    Business.objects.filter(pk=business.pk).update(invoice_sequence=F("invoice_sequence") + 1)
    business.refresh_from_db(fields=["invoice_sequence"])
    return f"{business.invoice_sequence:05d}"


def _payment_status(total: Decimal, paid: Decimal) -> str:
    if not paid:
        return SalesInvoice.PaymentStatus.UNPAID
    if paid >= total:
        return SalesInvoice.PaymentStatus.PAID
    return SalesInvoice.PaymentStatus.PARTIAL


def _clean_lines(
    lines: list[dict],
    *,
    seller_business: Business,
    currency: str,
    display_unit: str,
    values_are_display: bool,
) -> list[dict]:
    def storage_amount(value, label: str):
        try:
            return to_storage_amount(
                value, currency=currency, display_unit=display_unit, label=label
            )
        except InvoiceCalculationError as exc:
            raise InvoiceError(str(exc)) from exc

    sources = list(lines or [])
    if len(sources) > 100:
        raise InvoiceError("حداکثر ۱۰۰ ردیف در هر فاکتور مجاز است.")

    def order(source):
        try:
            return int(source.get("sort_order", 0))
        except (AttributeError, TypeError, ValueError):
            return 0

    cleaned: list[dict] = []
    for source in sorted(sources, key=order):
        if not source:
            continue
        if not isinstance(source, dict):
            raise InvoiceError("ساختار ردیف فاکتور معتبر نیست.")
        item = source.get("item")
        if item is not None and item.business_id != seller_business.id:
            raise InvoiceError("محصول انتخاب‌شده متعلق به این کسب‌وکار نیست.")
        name = str(source.get("product_name") or "").strip()
        if not name and item is not None:
            name = item.product.commercial_name
        if not name:
            raise InvoiceError("نام محصول هر ردیف را وارد کنید.")
        if len(name) > 200:
            raise InvoiceError("نام محصول هر ردیف حداکثر ۲۰۰ نویسه است.")
        unit = str(source.get("unit") or "متر مربع").strip()
        if unit not in ALLOWED_UNITS:
            raise InvoiceError("واحد ردیف معتبر نیست.")
        discount_type = source.get("discount_type") or DISCOUNT_NONE
        unit_price = source.get("unit_price")
        discount_value = source.get("discount_value") or 0
        if values_are_display:
            unit_price = storage_amount(unit_price, "قیمت واحد")
            if discount_type == DISCOUNT_AMOUNT:
                discount_value = storage_amount(discount_value, "تخفیف ردیف")
        cleaned.append(
            {
                "item": item,
                "product_name": name,
                "stone_type": str(
                    source.get("stone_type")
                    or (item.product.stone.name if item is not None else "")
                )[:100],
                "grade": str(source.get("grade") or "")[:50],
                "description": str(source.get("description") or "").strip(),
                "quantity": source.get("quantity"),
                "unit": unit,
                "unit_price": unit_price,
                "discount_type": discount_type,
                "discount_value": discount_value,
                "sort_order": len(cleaned),
            }
        )
        if len(cleaned[-1]["description"]) > 255:
            raise InvoiceError("توضیح هر ردیف حداکثر ۲۵۵ نویسه است.")
    return cleaned


def _calculate(
    lines: list[dict],
    *,
    currency: str,
    display_unit: str,
    values_are_display: bool,
    invoice_discount_type: str,
    invoice_discount_value,
    tax_amount,
    shipping_amount,
    adjustment_amount,
    paid_amount,
    previous_balance_snapshot=0,
    previous_balance_included=False,
):
    def normalized(value, label: str):
        if not values_are_display:
            return value
        return to_storage_amount(value, currency=currency, display_unit=display_unit, label=label)

    discount_value = invoice_discount_value
    try:
        validate_display_unit(currency, display_unit)
        if values_are_display and invoice_discount_type == DISCOUNT_AMOUNT:
            discount_value = normalized(invoice_discount_value, "تخفیف کلی")
        return calculate_invoice(
            lines,
            invoice_discount_type=invoice_discount_type,
            invoice_discount_value=discount_value,
            tax_amount=normalized(tax_amount, "مالیات"),
            shipping_amount=normalized(shipping_amount, "هزینه ارسال"),
            adjustment_amount=normalized(adjustment_amount, "تعدیل"),
            paid_amount=normalized(paid_amount, "مبلغ پرداخت‌شده"),
            previous_balance_snapshot=previous_balance_snapshot,
            previous_balance_included=previous_balance_included,
        )
    except InvoiceCalculationError as exc:
        raise InvoiceError(str(exc)) from exc


def _assign_totals(invoice: SalesInvoice, totals) -> None:
    for field, value in totals.as_dict().items():
        setattr(invoice, field, value)
    invoice.payment_status = _payment_status(totals.total_amount, totals.paid_amount)


def _replace_items(invoice: SalesInvoice, lines) -> None:
    invoice.items.all().delete()
    SalesInvoiceItem.objects.bulk_create(
        [
            SalesInvoiceItem(
                invoice=invoice,
                item=line.source.get("item"),
                product_name=line.source["product_name"],
                stone_type=line.source.get("stone_type", ""),
                grade=line.source.get("grade", ""),
                description=line.source.get("description", ""),
                quantity=line.quantity,
                unit=line.source.get("unit", "متر مربع"),
                unit_price=line.unit_price,
                gross_total=line.gross_total,
                discount_type=line.discount_type,
                discount_value=line.discount_value,
                discount_amount=line.discount_amount,
                line_total=line.line_total,
                sort_order=line.source.get("sort_order", index),
            )
            for index, line in enumerate(lines)
        ]
    )


def _previous_balance_for_trade(trade) -> tuple[Decimal, str, str]:
    if not trade.buyer_business_id:
        return Decimal("0"), SalesInvoice.BalanceState.SETTLED, trade.currency
    from apps.accounting.models import LedgerEntry

    entry = LedgerEntry.objects.filter(
        business=trade.seller_business,
        counterparty_business=trade.buyer_business,
        related_trade=trade,
    ).first()
    if entry is None:
        return Decimal("0"), SalesInvoice.BalanceState.SETTLED, trade.currency
    opening = entry.balance_after - entry.balance_delta
    if opening > 0:
        return opening, SalesInvoice.BalanceState.DEBTOR, entry.currency
    if opening < 0:
        return -opening, SalesInvoice.BalanceState.CREDITOR, entry.currency
    return Decimal("0"), SalesInvoice.BalanceState.SETTLED, entry.currency


@transaction.atomic
def create_invoice_for_trade(
    *, trade, membership: BusinessMembership, notes: str = "", issue: bool | None = None
) -> SalesInvoice:
    business = trade.seller_business
    _require_seller_may_invoice(business, membership)
    if issue is None:
        issue = membership.has_capability(INVOICE_MANAGE)
    return _create_invoice_for_trade(
        trade=trade, created_by=membership.user, notes=notes, issue=issue
    )


@transaction.atomic
def create_invoice_for_confirmed_trade(
    *, trade, membership: BusinessMembership, notes: str = ""
) -> SalesInvoice:
    if membership is None or not membership.has_capability(TRADE_CONFIRM):
        raise InvoiceError("اجازه تأیید معامله را ندارید.")
    if membership.business_id not in {trade.seller_business_id, trade.buyer_business_id}:
        raise InvoiceError("دسترسی نامعتبر است.")
    proposal = getattr(trade, "source_proposal", None)
    if proposal is None or proposal.status != "confirmed":
        raise InvoiceError("این معامله از توافق دوطرفه تأییدشده ایجاد نشده است.")
    try:
        require_entitlement(trade.seller_business, ISSUE_INVOICES)
    except EntitlementError as exc:
        raise InvoiceError(exc.message) from exc
    return _create_invoice_for_trade(
        trade=trade, created_by=membership.user, notes=notes, issue=True
    )


def _create_invoice_for_trade(*, trade, created_by, notes: str, issue: bool) -> SalesInvoice:
    business = trade.seller_business
    Business.objects.select_for_update().get(pk=business.pk)
    existing = SalesInvoice.objects.filter(trade=trade).first()
    if existing is not None:
        return existing
    settings_row = get_invoice_settings(business)
    previous, previous_state, previous_currency = _previous_balance_for_trade(trade)
    raw_lines = [
        {
            "item": line.item,
            "product_name": line.product_name,
            "stone_type": line.stone_type,
            "grade": line.grade,
            "quantity": line.quantity,
            "unit": line.unit,
            "unit_price": line.unit_price,
            "discount_type": DISCOUNT_NONE,
            "discount_value": 0,
            "sort_order": line.sort_order,
        }
        for line in trade.items.all()
    ]
    cleaned = _clean_lines(
        raw_lines,
        seller_business=business,
        currency=trade.currency,
        display_unit=trade.currency,
        values_are_display=False,
    )
    calculated, totals = _calculate(
        cleaned,
        currency=trade.currency,
        display_unit=trade.currency,
        values_are_display=False,
        invoice_discount_type=DISCOUNT_NONE,
        invoice_discount_value=0,
        tax_amount=0,
        shipping_amount=0,
        adjustment_amount=0,
        paid_amount=0,
        previous_balance_snapshot=previous,
        previous_balance_included=False,
    )
    if totals.total_amount != trade.total_amount:
        raise InvoiceError("جمع اقلام معامله با مبلغ تأییدشده سازگار نیست.")
    try:
        with transaction.atomic():
            invoice = SalesInvoice(
                seller_business=business,
                number=allocate_number(business) if issue else "",
                counterparty_type=(
                    SalesInvoice.Counterparty.BUSINESS
                    if trade.buyer_business_id
                    else SalesInvoice.Counterparty.CUSTOMER
                ),
                buyer_business=trade.buyer_business,
                customer_name=trade.customer_name,
                customer_phone=trade.customer_phone,
                buyer_name=trade.counterparty_label,
                buyer_phone=trade.customer_phone,
                trade=trade,
                issue_date=timezone.localdate(),
                status=SalesInvoice.Status.ISSUED if issue else SalesInvoice.Status.DRAFT,
                currency=trade.currency,
                display_unit=(
                    settings_row.default_display_unit
                    if trade.currency == settings_row.default_currency
                    else trade.currency
                ),
                notes=(notes or "").strip(),
                payment_terms=settings_row.payment_terms,
                previous_balance_state=previous_state,
                previous_balance_currency=previous_currency,
                seller_snapshot=seller_snapshot(business, settings_row) if issue else {},
                appearance_snapshot=appearance_snapshot(settings_row),
                created_by=created_by,
            )
            _assign_totals(invoice, totals)
            invoice.save()
            _replace_items(invoice, calculated)
    except IntegrityError:
        winner = SalesInvoice.objects.filter(trade=trade).first()
        if winner is None:
            raise
        logger.info("Concurrent invoice creation resolved to %s", winner.number)
        return winner
    return invoice


@transaction.atomic
def create_manual_invoice(
    *,
    business: Business,
    membership: BusinessMembership,
    lines: list[dict],
    buyer_business: Business | None = None,
    customer_name: str = "",
    customer_phone: str = "",
    buyer_address: str = "",
    notes: str = "",
    payment_terms: str = "",
    issue_date=None,
    issue: bool = True,
    currency: str = "IRR",
    display_unit: str = "IRR",
    invoice_discount_type: str = DISCOUNT_NONE,
    invoice_discount_value=0,
    tax_amount=0,
    shipping_amount=0,
    adjustment_amount=0,
    paid_amount=0,
    appearance: dict | None = None,
    buyer_signature=None,
    remove_buyer_signature: bool = False,
) -> SalesInvoice:
    _require_manage(business, membership)
    if buyer_business is not None:
        raise InvoiceError(
            "فروش به همکار باید از «توافق معامله» ثبت شود تا حساب‌ها به‌روز شوند."
        )
    customer_name = str(customer_name or "").strip()
    if not customer_name:
        raise InvoiceError("نام خریدار را وارد کنید.")
    if len(customer_name) > 150:
        raise InvoiceError("نام خریدار حداکثر ۱۵۰ نویسه است.")
    customer_phone = str(customer_phone or "").strip()
    if len(customer_phone) > 20:
        raise InvoiceError("شماره تماس خریدار حداکثر ۲۰ نویسه است.")
    settings_row = get_invoice_settings(business)
    cleaned = _clean_lines(
        lines,
        seller_business=business,
        currency=currency,
        display_unit=display_unit,
        values_are_display=True,
    )
    calculated, totals = _calculate(
        cleaned,
        currency=currency,
        display_unit=display_unit,
        values_are_display=True,
        invoice_discount_type=invoice_discount_type,
        invoice_discount_value=invoice_discount_value,
        tax_amount=tax_amount,
        shipping_amount=shipping_amount,
        adjustment_amount=adjustment_amount,
        paid_amount=paid_amount,
    )
    if issue:
        Business.objects.select_for_update().get(pk=business.pk)
    invoice = SalesInvoice(
        seller_business=business,
        number=allocate_number(business) if issue else "",
        counterparty_type=SalesInvoice.Counterparty.CUSTOMER,
        buyer_business=None,
        customer_name=customer_name,
        customer_phone=customer_phone,
        buyer_name=customer_name,
        buyer_phone=customer_phone,
        buyer_address=(buyer_address or "").strip(),
        issue_date=issue_date or timezone.localdate(),
        status=SalesInvoice.Status.ISSUED if issue else SalesInvoice.Status.DRAFT,
        currency=currency,
        display_unit=display_unit,
        notes=(notes or "").strip(),
        payment_terms=(payment_terms or settings_row.payment_terms).strip(),
        seller_snapshot=seller_snapshot(business, settings_row) if issue else {},
        appearance_snapshot=appearance_snapshot(settings_row, appearance),
        buyer_signature=(
            None if remove_buyer_signature and not buyer_signature else buyer_signature
        ),
        created_by=membership.user,
    )
    _assign_totals(invoice, totals)
    invoice.save()
    _replace_items(invoice, calculated)
    return invoice


@transaction.atomic
def update_draft_invoice(
    *, invoice: SalesInvoice, membership: BusinessMembership, lines: list[dict], **header
) -> SalesInvoice:
    invoice = SalesInvoice.objects.select_for_update().get(pk=invoice.pk)
    _require_manage(invoice.seller_business, membership)
    if invoice.status != SalesInvoice.Status.DRAFT:
        raise InvoiceError("فقط پیش‌نویس قابل ویرایش است.")
    business = invoice.seller_business
    currency = header.get("currency", invoice.currency)
    display_unit = header.get("display_unit", invoice.display_unit)
    cleaned = _clean_lines(
        lines,
        seller_business=business,
        currency=currency,
        display_unit=display_unit,
        values_are_display=True,
    )
    calculated, totals = _calculate(
        cleaned,
        currency=currency,
        display_unit=display_unit,
        values_are_display=True,
        invoice_discount_type=header.get("invoice_discount_type", DISCOUNT_NONE),
        invoice_discount_value=header.get("invoice_discount_value", 0),
        tax_amount=header.get("tax_amount", 0),
        shipping_amount=header.get("shipping_amount", 0),
        adjustment_amount=header.get("adjustment_amount", 0),
        paid_amount=header.get("paid_amount", 0),
        previous_balance_snapshot=invoice.previous_balance_snapshot,
        previous_balance_included=invoice.previous_balance_included,
    )
    settings_row = get_invoice_settings(business)
    invoice.customer_name = str(header.get("customer_name") or "").strip()
    if not invoice.customer_name:
        raise InvoiceError("نام خریدار را وارد کنید.")
    if len(invoice.customer_name) > 150:
        raise InvoiceError("نام خریدار حداکثر ۱۵۰ نویسه است.")
    invoice.buyer_name = invoice.customer_name
    invoice.customer_phone = str(header.get("customer_phone") or "").strip()
    if len(invoice.customer_phone) > 20:
        raise InvoiceError("شماره تماس خریدار حداکثر ۲۰ نویسه است.")
    invoice.buyer_phone = invoice.customer_phone
    invoice.buyer_address = (header.get("buyer_address") or "").strip()
    invoice.issue_date = header.get("issue_date") or invoice.issue_date
    invoice.currency = currency
    invoice.display_unit = display_unit
    invoice.notes = (header.get("notes") or "").strip()
    invoice.payment_terms = (
        header.get("payment_terms") or settings_row.payment_terms
    ).strip()
    invoice.appearance_snapshot = appearance_snapshot(
        settings_row, header.get("appearance")
    )
    if header.get("buyer_signature"):
        invoice.buyer_signature = header["buyer_signature"]
    elif header.get("remove_buyer_signature"):
        invoice.buyer_signature = None
    _assign_totals(invoice, totals)
    invoice.save()
    _replace_items(invoice, calculated)
    return invoice


@transaction.atomic
def issue_invoice(*, invoice: SalesInvoice, membership: BusinessMembership) -> SalesInvoice:
    invoice = SalesInvoice.objects.select_for_update().select_related("seller_business").get(
        pk=invoice.pk
    )
    _require_manage(invoice.seller_business, membership)
    if invoice.status == SalesInvoice.Status.ISSUED:
        return invoice
    if invoice.status == SalesInvoice.Status.CANCELLED:
        raise InvoiceError("فاکتور باطل‌شده قابل صدور نیست.")
    if (
        invoice.previous_balance_included
        and invoice.previous_balance_currency != invoice.currency
    ):
        raise InvoiceError("مانده با ارز متفاوت را نمی‌توان در مبلغ قابل پرداخت جمع کرد.")
    stored_lines = [
        {
            "item": line.item,
            "product_name": line.product_name,
            "stone_type": line.stone_type,
            "grade": line.grade,
            "description": line.description,
            "quantity": line.quantity,
            "unit": line.unit,
            "unit_price": line.unit_price,
            "discount_type": line.discount_type,
            "discount_value": line.discount_value,
            "sort_order": line.sort_order,
        }
        for line in invoice.items.all()
    ]
    cleaned = _clean_lines(
        stored_lines,
        seller_business=invoice.seller_business,
        currency=invoice.currency,
        display_unit=invoice.currency,
        values_are_display=False,
    )
    _calculated, totals = _calculate(
        cleaned,
        currency=invoice.currency,
        display_unit=invoice.currency,
        values_are_display=False,
        invoice_discount_type=invoice.invoice_discount_type,
        invoice_discount_value=invoice.invoice_discount_value,
        tax_amount=invoice.tax_amount,
        shipping_amount=invoice.shipping_amount,
        adjustment_amount=invoice.adjustment_amount,
        paid_amount=invoice.paid_amount,
        previous_balance_snapshot=invoice.previous_balance_snapshot,
        previous_balance_included=invoice.previous_balance_included,
    )
    _assign_totals(invoice, totals)
    settings_row = get_invoice_settings(invoice.seller_business)
    Business.objects.select_for_update().get(pk=invoice.seller_business_id)
    if not invoice.number:
        invoice.number = allocate_number(invoice.seller_business)
    invoice.seller_snapshot = seller_snapshot(invoice.seller_business, settings_row)
    selected_appearance = {
        key: value
        for key, value in (invoice.appearance_snapshot or {}).items()
        if key not in {"logo_name", "stamp_name", "signature_name"}
    }
    invoice.appearance_snapshot = appearance_snapshot(settings_row, selected_appearance)
    invoice.status = SalesInvoice.Status.ISSUED
    invoice.save()
    return invoice


@transaction.atomic
def cancel_invoice(*, invoice: SalesInvoice, membership: BusinessMembership) -> SalesInvoice:
    invoice = SalesInvoice.objects.select_for_update().get(pk=invoice.pk)
    _require_manage(invoice.seller_business, membership)
    if invoice.status == SalesInvoice.Status.DRAFT:
        raise InvoiceError("پیش‌نویس را حذف کنید؛ ابطال برای سند صادرشده است.")
    invoice.status = SalesInvoice.Status.CANCELLED
    invoice.save(update_fields=["status", "updated_at"])
    return invoice


@transaction.atomic
def duplicate_invoice(*, invoice: SalesInvoice, membership: BusinessMembership) -> SalesInvoice:
    _require_manage(invoice.seller_business, membership)
    if invoice.counterparty_type == SalesInvoice.Counterparty.BUSINESS:
        raise InvoiceError("توافق همکار را از صفحه همان توافق دوباره ثبت کنید؛ فاکتور همکار قابل تکثیر دستی نیست.")
    lines = [
        {
            "item": line.item,
            "product_name": line.product_name,
            "stone_type": line.stone_type,
            "grade": line.grade,
            "description": line.description,
            "quantity": line.quantity,
            "unit": line.unit,
            "unit_price": line.unit_price,
            "discount_type": line.discount_type,
            "discount_value": line.discount_value,
            "sort_order": line.sort_order,
        }
        for line in invoice.items.all()
    ]
    cleaned = _clean_lines(
        lines,
        seller_business=invoice.seller_business,
        currency=invoice.currency,
        display_unit=invoice.currency,
        values_are_display=False,
    )
    calculated, totals = _calculate(
        cleaned,
        currency=invoice.currency,
        display_unit=invoice.currency,
        values_are_display=False,
        invoice_discount_type=invoice.invoice_discount_type,
        invoice_discount_value=invoice.invoice_discount_value,
        tax_amount=invoice.tax_amount,
        shipping_amount=invoice.shipping_amount,
        adjustment_amount=invoice.adjustment_amount,
        paid_amount=0,
    )
    copy = SalesInvoice(
        seller_business=invoice.seller_business,
        number="",
        counterparty_type=invoice.counterparty_type,
        buyer_business=invoice.buyer_business,
        customer_name=invoice.customer_name,
        customer_phone=invoice.customer_phone,
        buyer_name=invoice.buyer_name,
        buyer_phone=invoice.buyer_phone,
        buyer_address=invoice.buyer_address,
        issue_date=timezone.localdate(),
        status=SalesInvoice.Status.DRAFT,
        currency=invoice.currency,
        display_unit=invoice.display_unit,
        notes=invoice.notes,
        payment_terms=invoice.payment_terms,
        appearance_snapshot=invoice.appearance_snapshot,
        created_by=membership.user,
    )
    _assign_totals(copy, totals)
    copy.save()
    _replace_items(copy, calculated)
    return copy


def save_as_template(
    *, invoice: SalesInvoice, name: str, membership: BusinessMembership
) -> InvoiceTemplate:
    _require_manage(invoice.seller_business, membership)
    if invoice.counterparty_type == SalesInvoice.Counterparty.BUSINESS:
        raise InvoiceError("فاکتور همکار فقط از توافق دوطرفه ساخته می‌شود و قالب دستی ندارد.")
    name = (name or "").strip()
    if not name:
        raise InvoiceError("نام قالب را وارد کنید.")
    payload = {
        "customer_name": invoice.customer_name,
        "customer_phone": invoice.customer_phone,
        "buyer_address": invoice.buyer_address,
        "notes": invoice.notes,
        "payment_terms": invoice.payment_terms,
        "currency": invoice.currency,
        "display_unit": invoice.display_unit,
        "invoice_discount_type": invoice.invoice_discount_type,
        "invoice_discount_value": str(invoice.invoice_discount_value),
        "tax_amount": str(invoice.tax_amount),
        "shipping_amount": str(invoice.shipping_amount),
        "adjustment_amount": str(invoice.adjustment_amount),
        "appearance": invoice.appearance_snapshot,
        "lines": [
            {
                "item_id": str(line.item_id) if line.item_id else "",
                "product_name": line.product_name,
                "stone_type": line.stone_type,
                "grade": line.grade,
                "description": line.description,
                "quantity": str(line.quantity),
                "unit": line.unit,
                "unit_price": str(line.unit_price),
                "discount_type": line.discount_type,
                "discount_value": str(line.discount_value),
                "sort_order": line.sort_order,
            }
            for line in invoice.items.all()
        ],
    }
    template, _ = InvoiceTemplate.objects.update_or_create(
        business=invoice.seller_business,
        name=name,
        defaults={"payload": payload, "created_by": membership.user},
    )
    return template


def safe_create_invoice_for_trade(*, trade, membership: BusinessMembership) -> SalesInvoice | None:
    try:
        return create_invoice_for_trade(trade=trade, membership=membership)
    except (InvoiceError, IntegrityError):
        logger.info("Invoice not created for trade %s", trade.id, exc_info=True)
        return None
