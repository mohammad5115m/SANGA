"""Buying and selling.

The rule this module exists to enforce: **accepting a purchase request is not a
sale.** Agreement and finalization are two deliberate actions, because a
preliminary "yes, I can do 180 at 1.6m" that never becomes a shipment must not
land in the ledger.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from apps.accounting.services import post_trade_entries
from apps.businesses.eligibility import NotOperationalError, require_operational
from apps.businesses.entitlements import (
    FINALIZE_SALES,
    RECEIVE_PURCHASE_REQUESTS,
    EntitlementError,
    require_entitlement,
)
from apps.businesses.models import Business, BusinessMembership
from apps.businesses.permissions import PURCHASE_REQUEST, SALE_FINALIZE
from apps.inventory.models import InventoryLot
from apps.inventory.policy import get_eligible_item
from apps.invoicing.services import safe_create_invoice_for_trade
from apps.notifications.models import Notification
from apps.notifications.services import notify_user

from .models import PurchaseRequest, Trade

logger = logging.getLogger(__name__)


class TradingError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def _require(membership: BusinessMembership, capability: str) -> None:
    if membership is None or not membership.has_capability(capability):
        raise TradingError("دسترسی لازم برای این عملیات را ندارید.")
    # A suspended or expired tenant does not buy either. Browse-only accounts can
    # send purchase requests without any seller entitlement, so without this the
    # buying side had no operational gate at all.
    try:
        require_operational(membership.business)
    except NotOperationalError as exc:
        raise TradingError(exc.message) from exc


def _require_plan(business: Business, entitlement: str) -> None:
    try:
        require_entitlement(business, entitlement)
    except EntitlementError as exc:
        raise TradingError(exc.message) from exc


def _quantize(value, places: str = "0.001") -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal(places))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise TradingError("مقدار واردشده معتبر نیست.") from exc


def _notify_business(business: Business, *, title: str, body: str, link: str) -> None:
    """Tell the people who can act on this.

    Owners and managers only: notifying every member of a large team about every
    request is how notification lists get ignored.
    """
    recipients = BusinessMembership.objects.filter(
        business=business,
        status=BusinessMembership.Status.ACTIVE,
        role__in=[BusinessMembership.Role.OWNER, BusinessMembership.Role.MANAGER],
    ).select_related("user")
    for membership in recipients:
        notify_user(
            user=membership.user,
            business=business,
            kind=Notification.Kind.GENERAL,
            title=title,
            body=body,
            link=link,
        )


# --- buyer side ---------------------------------------------------------------


@transaction.atomic
def create_purchase_request(
    *,
    buyer_business: Business,
    membership: BusinessMembership,
    item: InventoryLot,
    requested_qty_sqm,
    proposed_unit_price=None,
    buyer_note: str = "",
) -> PurchaseRequest:
    """Ask to buy a specific product.

    The item is re-resolved through the buyer-facing eligibility gate rather
    than trusted from the caller: a request must not be creatable against a
    product that has been hidden, marked unavailable or deleted since the page
    was rendered.
    """
    _require(membership, PURCHASE_REQUEST)
    if membership.business_id != buyer_business.id:
        raise TradingError("دسترسی نامعتبر است.")

    visible = get_eligible_item(
        audience="colleague",
        viewer_business=buyer_business,
        item_id=item.pk,
    )
    if visible is None:
        raise TradingError("این محصول دیگر برای خرید در دسترس نیست.")

    seller = visible.business
    _require_plan(seller, RECEIVE_PURCHASE_REQUESTS)

    qty = _quantize(requested_qty_sqm)
    if qty <= 0:
        raise TradingError("متراژ درخواستی باید بزرگ‌تر از صفر باشد.")

    price = None
    if proposed_unit_price not in (None, ""):
        price = _quantize(proposed_unit_price, "0.01")
        if price < 0:
            raise TradingError("قیمت پیشنهادی نمی‌تواند منفی باشد.")

    request = PurchaseRequest.objects.create(
        item=visible,
        seller_business=seller,
        buyer_business=buyer_business,
        created_by=membership.user,
        requested_qty_sqm=qty,
        proposed_unit_price=price,
        buyer_note=(buyer_note or "").strip(),
    )

    _notify_business(
        seller,
        title="درخواست خرید جدید",
        body=f"{buyer_business.name} برای «{visible.product.commercial_name}» درخواست خرید ثبت کرد.",
        link=f"/app/trading/received/{request.id}/",
    )
    logger.info("Purchase request created id=%s seller=%s buyer=%s", request.id, seller.id, buyer_business.id)
    return request


@transaction.atomic
def cancel_purchase_request(*, request: PurchaseRequest, membership: BusinessMembership) -> PurchaseRequest:
    _require(membership, PURCHASE_REQUEST)
    if request.buyer_business_id != membership.business_id:
        raise TradingError("این درخواست متعلق به کسب‌وکار شما نیست.")
    if not request.is_open:
        raise TradingError("این درخواست دیگر قابل لغو نیست.")

    request.status = PurchaseRequest.Status.CANCELLED
    request.decided_at = timezone.now()
    request.save(update_fields=["status", "decided_at", "updated_at"])
    return request


# --- seller side --------------------------------------------------------------


@transaction.atomic
def respond_to_purchase_request(
    *,
    request: PurchaseRequest,
    membership: BusinessMembership,
    accept: bool,
    final_qty_sqm=None,
    final_unit_price=None,
    seller_note: str = "",
) -> PurchaseRequest:
    """Agree or decline, optionally adjusting quantity and price.

    Accepting records agreement and nothing else. No Trade, no ledger entry, no
    stock change — those wait for :func:`finalize_sale`.
    """
    _require(membership, PURCHASE_REQUEST)
    if request.seller_business_id != membership.business_id:
        raise TradingError("این درخواست متعلق به کسب‌وکار شما نیست.")
    if request.status != PurchaseRequest.Status.SENT:
        raise TradingError("به این درخواست قبلاً پاسخ داده شده است.")

    request.seller_note = (seller_note or "").strip()
    request.decided_at = timezone.now()

    if not accept:
        request.status = PurchaseRequest.Status.REJECTED
        request.save(update_fields=["status", "seller_note", "decided_at", "updated_at"])
        _notify_business(
            request.buyer_business,
            title="درخواست خرید رد شد",
            body=f"{request.seller_business.name} درخواست شما را نپذیرفت.",
            link=f"/app/trading/sent/{request.id}/",
        )
        return request

    if final_qty_sqm not in (None, ""):
        qty = _quantize(final_qty_sqm)
        if qty <= 0:
            raise TradingError("متراژ نهایی باید بزرگ‌تر از صفر باشد.")
        request.final_qty_sqm = qty
    if final_unit_price not in (None, ""):
        price = _quantize(final_unit_price, "0.01")
        if price < 0:
            raise TradingError("قیمت نهایی نمی‌تواند منفی باشد.")
        request.final_unit_price = price

    if request.agreed_unit_price is None:
        raise TradingError("برای پذیرش درخواست، قیمت نهایی را وارد کنید.")

    request.status = PurchaseRequest.Status.ACCEPTED
    request.save(
        update_fields=[
            "status",
            "final_qty_sqm",
            "final_unit_price",
            "seller_note",
            "decided_at",
            "updated_at",
        ]
    )
    _notify_business(
        request.buyer_business,
        title="درخواست خرید پذیرفته شد",
        body=f"{request.seller_business.name} با درخواست شما موافقت کرد.",
        link=f"/app/trading/sent/{request.id}/",
    )
    return request


@transaction.atomic
def finalize_sale(
    *,
    request: PurchaseRequest,
    membership: BusinessMembership,
    note: str = "",
) -> Trade:
    """Turn an accepted request into a Trade.

    The one authoritative commercial event. It is the point at which the ledger
    is posted (Phase 5 attaches that), so it must happen exactly once — the
    ``OneToOneField`` from Trade to PurchaseRequest plus the status transition
    inside this transaction is what guarantees that under a double-click or a
    retried POST.

    Stock is deliberately **not** decremented. SANGA does not know whether this
    was the only sale of that product.
    """
    _require(membership, SALE_FINALIZE)
    if request.seller_business_id != membership.business_id:
        raise TradingError("این درخواست متعلق به کسب‌وکار شما نیست.")

    # Lock the row so two concurrent finalizations serialize; the second sees
    # COMPLETED and stops.
    locked = (
        PurchaseRequest.objects.select_for_update()
        .select_related("seller_business", "buyer_business", "item", "item__product")
        .get(pk=request.pk)
    )
    # Read the plan from the freshly-loaded row rather than from whatever the
    # caller had in memory: a subscription that lapsed since the page rendered
    # must block the sale.
    _require_plan(locked.seller_business, FINALIZE_SALES)
    if locked.status == PurchaseRequest.Status.COMPLETED:
        raise TradingError("این فروش قبلاً نهایی شده است.")
    if locked.status != PurchaseRequest.Status.ACCEPTED:
        raise TradingError("فقط درخواست‌های پذیرفته‌شده قابل نهایی شدن هستند.")

    unit_price = locked.agreed_unit_price
    if unit_price is None:
        raise TradingError("قیمت نهایی مشخص نیست.")
    quantity = locked.agreed_qty_sqm
    total = (unit_price * quantity).quantize(Decimal("0.01"))

    item = locked.item
    trade = Trade.objects.create(
        seller_business=locked.seller_business,
        counterparty_type=Trade.Counterparty.BUSINESS,
        buyer_business=locked.buyer_business,
        item=item,
        purchase_request=locked,
        # Snapshot: this is what was sold, whatever the product becomes later.
        product_name=item.product.commercial_name,
        stone_type=item.product.stone_type,
        grade=item.grade,
        quantity_sqm=quantity,
        unit_price=unit_price,
        total_amount=total,
        currency=locked.currency,
        note=(note or "").strip(),
        finalized_at=timezone.now(),
        created_by=membership.user,
    )

    locked.status = PurchaseRequest.Status.COMPLETED
    locked.save(update_fields=["status", "updated_at"])

    # One transaction covers: create Trade → post both books → link an invoice.
    # If the ledger post fails, the whole finalization rolls back rather than
    # leaving a sale that never reached the books.
    post_trade_entries(trade=trade, membership=membership)
    safe_create_invoice_for_trade(trade=trade, membership=membership)

    _notify_business(
        locked.buyer_business,
        title="فروش نهایی شد",
        body=f"{locked.seller_business.name} فروش «{trade.product_name}» را نهایی کرد.",
        link=f"/app/trading/sent/{locked.id}/",
    )
    logger.info("Trade finalized id=%s request=%s total=%s", trade.id, locked.id, total)
    return trade


@transaction.atomic
def record_direct_sale(
    *,
    seller_business: Business,
    membership: BusinessMembership,
    item: InventoryLot | None,
    quantity_sqm,
    unit_price,
    buyer_business: Business | None = None,
    customer_name: str = "",
    customer_phone: str = "",
    product_name: str = "",
    note: str = "",
) -> Trade:
    """Record a sale that did not come through a purchase request.

    Most sales still happen over the phone. Forcing the seller to invent a
    request first would make them stop recording sales at all.
    """
    _require(membership, SALE_FINALIZE)
    if membership.business_id != seller_business.id:
        raise TradingError("دسترسی نامعتبر است.")
    _require_plan(seller_business, FINALIZE_SALES)

    if item is not None and item.business_id != seller_business.id:
        raise TradingError("این محصول متعلق به کسب‌وکار شما نیست.")

    quantity = _quantize(quantity_sqm)
    if quantity <= 0:
        raise TradingError("متراژ باید بزرگ‌تر از صفر باشد.")
    price = _quantize(unit_price, "0.01")
    if price < 0:
        raise TradingError("قیمت نمی‌تواند منفی باشد.")

    if buyer_business is not None:
        if buyer_business.id == seller_business.id:
            raise TradingError("خریدار و فروشنده نمی‌توانند یکی باشند.")
        counterparty_type = Trade.Counterparty.BUSINESS
        customer_name = ""
        customer_phone = ""
    else:
        counterparty_type = Trade.Counterparty.CUSTOMER
        customer_name = (customer_name or "").strip()
        if not customer_name:
            raise TradingError("نام مشتری را وارد کنید.")

    snapshot_name = (product_name or "").strip() or (item.product.commercial_name if item else "")
    if not snapshot_name:
        raise TradingError("نام محصول را وارد کنید.")

    trade = Trade.objects.create(
        seller_business=seller_business,
        counterparty_type=counterparty_type,
        buyer_business=buyer_business,
        customer_name=customer_name,
        customer_phone=(customer_phone or "").strip(),
        item=item,
        product_name=snapshot_name,
        stone_type=item.product.stone_type if item else "",
        grade=item.grade if item else "",
        quantity_sqm=quantity,
        unit_price=price,
        total_amount=(price * quantity).quantize(Decimal("0.01")),
        note=(note or "").strip(),
        finalized_at=timezone.now(),
        created_by=membership.user,
    )

    # A finalized sale is a finalized sale, however it was reached. Posting only
    # for request-driven sales would leave the books wrong for every deal agreed
    # over the phone — which is most of them. A walk-in customer has no account,
    # so post_trade_entries posts nothing for those.
    post_trade_entries(trade=trade, membership=membership)
    safe_create_invoice_for_trade(trade=trade, membership=membership)
    logger.info("Direct sale recorded trade=%s seller=%s total=%s", trade.id, seller_business.id, trade.total_amount)
    return trade
