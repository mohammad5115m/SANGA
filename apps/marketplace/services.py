from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from apps.businesses.models import BusinessMembership
from apps.businesses.permissions import TRADE_PROPOSE
from apps.core.formatting import format_rial
from apps.inventory.freshness import stock_view
from apps.inventory.models import InventoryLot
from apps.notifications.services import notify_business
from apps.pricing.services import effective_price, resolve_prices_for_viewer

from .models import PartnerInquiry, PartnerInquiryBatch, PartnerInquiryItem


class MarketplaceError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def b2b_price_context(lot: InventoryLot, viewer_business=None) -> dict:
    """Colleague-facing price payload: B2B tier only.

    Returns a flat dict with no tier keys, so a template cannot walk it to find
    the B2C number, and an expired or inquiry-mode price arrives already
    reduced to «استعلام قیمت» with no amount attached.
    """
    prices = resolve_prices_for_viewer(lot, "b2b_partner", viewer_business=viewer_business)
    price = effective_price(prices, "b2b_partner")
    if price is None or price.amount is None:
        return {
            "has_price": False,
            "amount": None,
            "currency": None,
            "label": "استعلام قیمت",
            "is_special": False,
        }
    return {
        "has_price": True,
        "amount": price.amount,
        "currency": price.currency,
        "label": format_rial(price.amount),
        "is_special": price.is_special,
        "special_until": price.special_until,
    }


def marketplace_lot_card(lot: InventoryLot, viewer_business=None) -> dict:
    primary = next((m for m in lot.media.all() if m.is_primary), None) or next(iter(lot.media.all()), None)
    return {
        "lot": lot,
        "product": lot.product,
        "supplier": lot.business,
        "price": b2b_price_context(lot, viewer_business),
        "stock": stock_view(lot),
        "primary_media": primary,
    }


def _quantity(value) -> Decimal:
    try:
        result = Decimal(str(value)).quantize(Decimal("0.001"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MarketplaceError("مقدار درخواستی معتبر نیست.") from exc
    if result <= 0:
        raise MarketplaceError("مقدار درخواستی باید بزرگ‌تر از صفر باشد.")
    return result


@transaction.atomic
def create_grouped_inquiries(
    *, buyer_business, user, selections: list[dict], submission_id=None, note: str = ""
) -> PartnerInquiryBatch:
    """Create one inquiry per seller while keeping a single buyer submission."""
    submission_id = submission_id or uuid.uuid4()
    existing = PartnerInquiryBatch.objects.filter(buyer_business=buyer_business, submission_id=submission_id).first()
    if existing:
        return existing
    lot_ids = [row.get("lot_id") for row in selections]
    from .selectors import marketplace_lots_for

    lots = {
        str(lot.id): lot
        for lot in marketplace_lots_for(buyer_business)
        .filter(id__in=lot_ids)
        .select_related("business", "product__stone")
    }
    rows = []
    for raw in selections:
        lot = lots.get(str(raw.get("lot_id")))
        if lot is None or lot.business_id == buyer_business.id:
            raise MarketplaceError("یکی از محصولات انتخاب‌شده در بازار همکاران موجود نیست.")
        rows.append((lot, _quantity(raw.get("quantity"))))
    if not rows:
        raise MarketplaceError("حداقل یک محصول را انتخاب کنید.")
    batch = PartnerInquiryBatch.objects.create(
        buyer_business=buyer_business, submission_id=submission_id, created_by=user
    )
    grouped: dict[str, list[tuple[InventoryLot, Decimal]]] = {}
    for lot, quantity in rows:
        grouped.setdefault(str(lot.business_id), []).append((lot, quantity))
    for seller_id, seller_rows in grouped.items():
        inquiry = PartnerInquiry.objects.create(
            batch=batch,
            buyer_business=buyer_business,
            seller_business_id=seller_id,
            buyer_note=str(note or "").strip(),
            sent_at=timezone.now(),
        )
        for index, (lot, quantity) in enumerate(seller_rows):
            price = b2b_price_context(lot, buyer_business)
            stock = stock_view(lot)
            PartnerInquiryItem.objects.create(
                inquiry=inquiry,
                item=lot,
                product_name=lot.product.commercial_name,
                stone_type=lot.product.stone.name,
                quantity_requested=quantity,
                unit="متر مربع",
                availability_snapshot=stock.label,
                availability_checked_at=lot.stock_confirmed_at,
                price_snapshot=price["amount"],
                currency=price["currency"] or "IRR",
                price_requires_confirmation=True,
                sort_order=index,
            )
        transaction.on_commit(
            lambda target=inquiry.seller_business, inquiry_id=inquiry.id: notify_business(
                target,
                capability=TRADE_PROPOSE,
                title="استعلام جدید بازار همکاران",
                body="یک خریدار برای محصولات شما مقدار و اطلاعات درخواست کرده است.",
                link=f"/app/marketplace/inquiries/{inquiry_id}/",
            )
        )
    return batch


@transaction.atomic
def respond_to_inquiry(
    *, inquiry: PartnerInquiry, membership: BusinessMembership, offers: dict, note: str = ""
) -> PartnerInquiry:
    inquiry = PartnerInquiry.objects.select_for_update().prefetch_related("items").get(pk=inquiry.pk)
    if membership.business_id != inquiry.seller_business_id:
        raise MarketplaceError("فقط فروشنده می‌تواند به این استعلام پاسخ دهد.")
    if inquiry.status not in {PartnerInquiry.Status.SENT, PartnerInquiry.Status.RESPONDED}:
        raise MarketplaceError("این استعلام قابل پاسخ‌گویی نیست.")
    for item in inquiry.items.all():
        offer = offers.get(str(item.id), {})
        item.offered_quantity = _quantity(offer.get("quantity", item.quantity_requested))
        try:
            price = Decimal(str(offer.get("unit_price"))).quantize(Decimal("0.01"))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise MarketplaceError("قیمت پیشنهادی همه اقلام را وارد کنید.") from exc
        if price <= 0:
            raise MarketplaceError("قیمت پیشنهادی باید بزرگ‌تر از صفر باشد.")
        item.offered_unit_price = price
        item.seller_note = str(offer.get("note", "")).strip()
        item.save(update_fields=["offered_quantity", "offered_unit_price", "seller_note"])
    inquiry.status = PartnerInquiry.Status.RESPONDED
    inquiry.seller_note = str(note or "").strip()
    inquiry.responded_at = timezone.now()
    inquiry.save(update_fields=["status", "seller_note", "responded_at"])
    transaction.on_commit(
        lambda target=inquiry.buyer_business, inquiry_id=inquiry.id: notify_business(
            target,
            capability=TRADE_PROPOSE,
            title="پاسخ استعلام بازار آماده است",
            body="فروشنده مقدار و قیمت پیشنهادی را ثبت کرده است.",
            link=f"/app/marketplace/inquiries/{inquiry_id}/",
        )
    )
    return inquiry


@transaction.atomic
def convert_inquiry_to_invoice(*, inquiry: PartnerInquiry, membership: BusinessMembership):
    inquiry = (
        PartnerInquiry.objects.select_for_update()
        .select_related("seller_business", "buyer_business")
        .prefetch_related("items__item")
        .get(pk=inquiry.pk)
    )
    if membership.business_id != inquiry.seller_business_id:
        raise MarketplaceError("فقط فروشنده می‌تواند استعلام را به فاکتور تبدیل کند.")
    if inquiry.converted_invoice_id:
        return inquiry.converted_invoice
    if inquiry.status != PartnerInquiry.Status.RESPONDED:
        raise MarketplaceError("ابتدا قیمت و مقدار پیشنهادی را ثبت کنید.")
    items = list(inquiry.items.all())
    total = sum((item.offered_quantity * item.offered_unit_price for item in items), Decimal("0"))
    currency = items[0].currency or "IRR"
    if any((item.currency or "IRR") != currency for item in items):
        raise MarketplaceError("همه اقلام یک فاکتور باید ارز یکسان داشته باشند.")
    from apps.invoicing.models import SalesInvoice
    from apps.invoicing.partner_services import create_partner_draft

    invoice = create_partner_draft(
        business=inquiry.seller_business,
        membership=membership,
        buyer_business=inquiry.buyer_business,
        lines=[
            {
                "item": item.item,
                "product_name": item.product_name,
                "stone_type": item.stone_type,
                "quantity": item.offered_quantity,
                "unit": item.unit,
                "unit_price": item.offered_unit_price,
                "sort_order": item.sort_order,
            }
            for item in items
        ],
        currency=currency,
        display_unit=currency,
        settlement_method=SalesInvoice.SettlementMethod.CREDIT,
        credit_amount=total,
        notes=f"برگرفته از استعلام همکار {inquiry.id}",
        submission_id=inquiry.id,
    )
    inquiry.status = PartnerInquiry.Status.CONVERTED
    inquiry.converted_invoice = invoice
    inquiry.converted_at = timezone.now()
    inquiry.save(update_fields=["status", "converted_invoice", "converted_at"])
    return invoice
