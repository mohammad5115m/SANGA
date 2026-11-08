from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import ROUND_HALF_UP

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.businesses.permissions import INVOICE_CREATE, PRICES_VIEW
from apps.inquiries.models import Inquiry, InquiryItem
from apps.inquiries.services import create_inquiry
from apps.invoicing.models import SalesInvoice
from apps.invoicing.services import InvoiceError, create_manual_invoice
from apps.pricing.services import MAX_PRICE_AMOUNT, lock_price_lot

from .models import ResaleOffer, ResaleRequest
from .pricing import CENT, ResaleError, current_terms, offer_price
from .selectors import offer_queryset
from .services import parse_quantity, require_actor, require_available_quantity

UNIT = "متر مربع"


def locked_offer(*, offer_id, retailer_id):
    offer = ResaleOffer.objects.select_related("rule__lot").filter(
        pk=offer_id, grant__retailer_id=retailer_id,
    ).first()
    if offer is None:
        raise ResaleError("این محصول در ویترین در دسترس نیست.")
    lock_price_lot(offer.rule.lot)
    offer = offer_queryset().filter(pk=offer_id, grant__retailer_id=retailer_id).first()
    if offer is None:
        raise ResaleError("این محصول در ویترین در دسترس نیست.")
    return offer


@transaction.atomic
def submit_request(*, offer_id, retailer, quantity, submission_id, name, phone, message="", verified=False):
    offer = locked_offer(offer_id=offer_id, retailer_id=retailer.pk)
    quantity = parse_quantity(quantity)
    require_available_quantity(offer.rule.lot, quantity)
    key = uuid.uuid5(uuid.NAMESPACE_URL, f"sanga-resale:{offer.pk}:{submission_id}")
    inquiry = create_inquiry(
        business=retailer, name=name, phone=phone, message=message, verified=verified,
        submission_id=key, source=Inquiry.Source.OTHER,
    )
    order = ResaleRequest.objects.filter(inquiry=inquiry).first()
    if order is not None:
        return order
    if inquiry.source != Inquiry.Source.OTHER or inquiry.items.exists():
        raise ResaleError("شناسهٔ درخواست قابل استفاده نیست. صفحه را دوباره باز کنید.")
    amount = offer_price(offer, current_terms(offer.rule)).amount
    InquiryItem.objects.create(
        inquiry=inquiry, product_name=offer.rule.lot.product.commercial_name, requested_qty_sqm=quantity,
    )
    return ResaleRequest.objects.create(
        inquiry=inquiry, offer=offer, warehouse=offer.grant.program.warehouse,
        quantity=quantity, displayed_amount=amount,
    )


def active_quote(offer, *, at=None):
    at = at or timezone.now()
    terms = current_terms(offer.rule, at=at)
    price = offer_price(offer, terms, at=at)
    if price.amount is None:
        raise ResaleError("ابتدا قیمت قابل عرضه را در پنل همکار به‌روز کنید.")
    return terms.wholesale, price.amount, min(price.expires_at, at + timedelta(hours=24))


@transaction.atomic
def request_purchase(*, order_id, retailer, membership, expected_wholesale, expected_retail):
    actor = require_actor(membership, retailer.pk, INVOICE_CREATE)
    if not actor.has_capability(PRICES_VIEW):
        raise ResaleError("برای تأیید نرخ خرید به دسترسی مشاهدهٔ قیمت نیاز دارید.")
    order = ResaleRequest.objects.select_for_update(of=("self",)).select_related("wholesale_invoice").get(
        pk=order_id, inquiry__business=retailer,
    )
    if not order.can_request_purchase:
        raise ResaleError("تا تعیین وضعیت فاکتورهای قبلی نمی‌توان برای این درخواست خرید دیگری ثبت کرد.")
    if order.offer_id is None:
        raise ResaleError("عرضهٔ این محصول متوقف شده است.")
    offer = locked_offer(offer_id=order.offer_id, retailer_id=retailer.pk)
    require_available_quantity(offer.rule.lot, order.quantity)
    wholesale, retail, expiry = active_quote(offer)
    if wholesale != expected_wholesale or retail != expected_retail:
        raise ResaleError("قیمت تغییر کرده است؛ نرخ تازه را بررسی و دوباره تأیید کنید.")
    if _total(retail, order.quantity) > MAX_PRICE_AMOUNT:
        raise ResaleError("مبلغ این متراژ از سقف فاکتور بیشتر است.")
    order.wholesale_amount = wholesale
    order.retail_amount = retail
    order.quote_until = expiry
    order.requested_at = timezone.now()
    order.wholesale_invoice = None
    order.save(update_fields=["wholesale_amount", "retail_amount", "quote_until", "requested_at", "wholesale_invoice"])
    return order


def _total(amount, quantity):
    return (amount * quantity).quantize(CENT, rounding=ROUND_HALF_UP)


def _line(order, amount, item=None):
    return {
        "item": item, "product_name": order.inquiry.items.get().product_name,
        "quantity": order.quantity, "unit": UNIT, "unit_price": amount,
    }


def _live_purchase(order):
    if (
        order.offer_id is None or order.wholesale_amount is None
        or order.quote_until is None or order.quote_until <= timezone.now()
    ):
        raise ResaleError("مهلت نرخ خرید گذشته است؛ همکار باید درخواست را با نرخ معتبر تأیید کند.")
    offer = offer_queryset().filter(
        pk=order.offer_id, grant__retailer_id=order.inquiry.business_id,
        grant__program__warehouse_id=order.warehouse_id,
    ).first()
    if offer is None:
        raise ResaleError("محصول یا مجوز همکاری دیگر در دسترس نیست.")
    require_available_quantity(offer.rule.lot, order.quantity)
    return offer


@transaction.atomic
def make_wholesale_invoice(*, order_id, warehouse, membership):
    from apps.invoicing.partner_services import create_partner_draft

    require_actor(membership, warehouse.pk, INVOICE_CREATE)
    order = ResaleRequest.objects.select_for_update(of=("self",)).select_related("inquiry__business").get(
        pk=order_id, warehouse=warehouse, requested_at__isnull=False,
    )
    if order.wholesale_invoice_id:
        return order.wholesale_invoice
    offer = _live_purchase(order)
    invoice = create_partner_draft(
        business=warehouse, membership=membership, buyer_business=order.inquiry.business,
        lines=[_line(order, order.wholesale_amount, item=offer.rule.lot)],
        credit_amount=_total(order.wholesale_amount, order.quantity),
    )
    order.wholesale_invoice = invoice
    order.save(update_fields=["wholesale_invoice"])
    return invoice


@transaction.atomic
def make_retail_invoice(*, order_id, retailer, membership):
    require_actor(membership, retailer.pk, INVOICE_CREATE)
    order = ResaleRequest.objects.select_for_update(of=("self",)).select_related("inquiry", "wholesale_invoice").get(
        pk=order_id, inquiry__business=retailer,
    )
    if order.retail_invoice_id:
        return order.retail_invoice
    if not order.wholesale_invoice or order.wholesale_invoice.status != SalesInvoice.Status.CONFIRMED:
        raise ResaleError("ابتدا فاکتور خرید همکاری را از انبار دریافت و تأیید کنید.")
    invoice = create_manual_invoice(
        business=retailer, membership=membership,
        customer_name=order.inquiry.name, customer_phone=order.inquiry.phone,
        lines=[_line(order, order.retail_amount)], issue=False, paid_amount=0,
    )
    order.retail_invoice = invoice
    order.save(update_fields=["retail_invoice"])
    return invoice


def linked_order(invoice):
    return ResaleRequest.objects.filter(
        Q(wholesale_invoice_id=invoice.pk) | Q(retail_invoice_id=invoice.pk),
    ).select_related("inquiry", "wholesale_invoice").first()


def validate_invoice(invoice, *, sale):
    """Before posting, preserve the accepted product, unit, quantity and net price."""
    order = linked_order(invoice)
    if order is None:
        return
    try:
        wholesale = order.wholesale_invoice_id == invoice.pk
        if sale == wholesale:
            raise ResaleError("نوع طرف حساب فاکتور عرضهٔ انبار قابل تغییر نیست.")
        item_id = None
        if wholesale:
            offer = _live_purchase(order)
            item_id = offer.rule.lot_id
            if (
                invoice.seller_business_id != order.warehouse_id
                or invoice.buyer_business_id != order.inquiry.business_id
                or invoice.counterparty_type != SalesInvoice.Counterparty.BUSINESS
            ):
                raise ResaleError("طرف حساب فاکتور همکاری با درخواست منطبق نیست.")
        elif (
            invoice.counterparty_type != SalesInvoice.Counterparty.CUSTOMER
            or not order.wholesale_invoice or order.wholesale_invoice.status != SalesInvoice.Status.CONFIRMED
            or invoice.seller_business_id != order.inquiry.business_id
            or invoice.buyer_phone != order.inquiry.phone
        ):
            raise ResaleError("خرید تأییدشده و طرف حساب مشتری را بررسی کنید.")
        amount = order.wholesale_amount if wholesale else order.retail_amount
        items = list(invoice.items.all())
        if (
            len(items) != 1 or items[0].item_id != item_id
            or items[0].product_name != order.inquiry.items.get().product_name
            or items[0].quantity != order.quantity or items[0].unit != UNIT
            or items[0].unit_price != amount or items[0].discount_amount or invoice.invoice_discount_amount
            or invoice.currency != "IRR"
        ):
            raise ResaleError("کالا، متراژ و مبلغ توافق‌شدهٔ این فاکتور قابل تغییر یا تخفیف دوباره نیست.")
    except ResaleError as exc:
        raise InvoiceError(str(exc)) from exc


def refuse_detached_copy(invoice):
    if linked_order(invoice) is not None:
        raise InvoiceError("برای فروش از انبار از درخواست اصلی استفاده کنید؛ ساخت نسخهٔ مستقل مجاز نیست.")
