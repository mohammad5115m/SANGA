"""Buying and selling.

Current colleague trades start as bilateral ``TradeProposal`` records. A
proposal is financially inert until the non-initiating party confirms it; that
single atomic action creates the Trade, both books and one issued invoice.

The request-era services below remain for historical records and compatibility.
No current UI creates, answers or finalizes a ``PurchaseRequest``.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounting.services import post_trade_entries
from apps.businesses.eligibility import (
    NotOperationalError,
    business_is_network_eligible,
    require_operational,
)
from apps.businesses.entitlements import (
    FINALIZE_SALES,
    RECEIVE_PURCHASE_REQUESTS,
    EntitlementError,
    require_entitlement,
)
from apps.businesses.models import Business, BusinessMembership
from apps.businesses.permissions import (
    PURCHASE_REQUEST,
    SALE_FINALIZE,
    TRADE_CONFIRM,
    TRADE_PROPOSE,
)
from apps.inventory.models import InventoryLot
from apps.inventory.policy import get_eligible_item
from apps.invoicing.services import create_invoice_for_confirmed_trade, safe_create_invoice_for_trade
from apps.notifications.services import notify_business

from .models import PurchaseRequest, Trade, TradeItem, TradeProposal, TradeProposalItem

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


def _clean_line(line: dict, *, seller_business: Business) -> dict:
    """Validate one sale line and freeze what it describes.

    The snapshot is taken here rather than read back through ``item`` later,
    because a product may be renamed, repriced or deleted between this sale and
    the next time anyone opens it.
    """
    item = line.get("item")
    if item is not None and item.business_id != seller_business.id:
        raise TradingError("این محصول متعلق به کسب‌وکار شما نیست.")

    name = (line.get("product_name") or "").strip() or (item.product.commercial_name if item else "")
    if not name:
        raise TradingError("نام محصول هر ردیف را وارد کنید.")

    quantity = _quantize(line.get("quantity"))
    if quantity <= 0:
        raise TradingError("متراژ هر ردیف باید بزرگ‌تر از صفر باشد.")
    unit_price = _quantize(line.get("unit_price"), "0.01")
    if unit_price <= 0:
        raise TradingError("قیمت باید بزرگ‌تر از صفر باشد.")

    return {
        "item": item,
        "product_name": name,
        "stone_type": (line.get("stone_type") or (item.product.stone.name if item else "")),
        "grade": (line.get("grade") or ""),
        "quantity": quantity,
        "unit_price": unit_price,
        # Rounded per line, then summed. Summing first and rounding once would
        # make the invoice's own rows fail to add up to its total.
        "line_total": (quantity * unit_price).quantize(Decimal("0.01")),
    }


def _write_lines(trade: Trade, lines: list[dict]) -> None:
    TradeItem.objects.bulk_create(
        [
            TradeItem(
                trade=trade,
                item=line["item"],
                product_name=line["product_name"],
                stone_type=line["stone_type"],
                grade=line["grade"],
                quantity=line["quantity"],
                unit_price=line["unit_price"],
                line_total=line["line_total"],
                sort_order=index,
            )
            for index, line in enumerate(lines)
        ]
    )


def _header_snapshot(lines: list[dict]) -> dict:
    """The legacy single-line columns on ``Trade``.

    Populated only when there is exactly one line, which covers every historical
    row and every request-driven sale, so existing readers keep working. Left
    blank for a multi-line sale rather than filled with the first line's values,
    which would describe the sale wrongly.
    """
    if len(lines) != 1:
        return {}
    line = lines[0]
    return {
        "item": line["item"],
        "product_name": line["product_name"],
        "stone_type": line["stone_type"],
        "grade": line["grade"],
        "quantity_sqm": line["quantity"],
        "unit_price": line["unit_price"],
    }


def _write_proposal_lines(proposal: TradeProposal, lines: list[dict]) -> None:
    TradeProposalItem.objects.bulk_create(
        [
            TradeProposalItem(
                proposal=proposal,
                item=line["item"],
                product_name=line["product_name"],
                stone_type=line["stone_type"],
                grade=line["grade"],
                quantity=line["quantity"],
                unit_price=line["unit_price"],
                line_total=line["line_total"],
                sort_order=index,
            )
            for index, line in enumerate(lines)
        ]
    )


def _notify_business(business: Business, *, capability: str, title: str, body: str, link: str) -> None:
    """Tell the members who hold the capability this notification is about.

    Was OWNER and MANAGER by role, which meant the default ``staff`` salesperson
    — who holds ``purchase.request`` and ``sale.finalize`` and does this work —
    never heard that a purchase request had arrived.
    """
    notify_business(business, capability=capability, title=title, body=body, link=link)


# --- bilateral offline agreements -------------------------------------------


def _validate_proposal_parties(
    *,
    seller_business: Business,
    buyer_business: Business,
    membership: BusinessMembership,
) -> None:
    if seller_business.id == buyer_business.id:
        raise TradingError("خریدار و فروشنده نمی‌توانند یکی باشند.")
    if membership.business_id not in {seller_business.id, buyer_business.id}:
        raise TradingError("فقط یکی از دو طرف معامله می‌تواند این توافق را ثبت کند.")
    other = buyer_business if membership.business_id == seller_business.id else seller_business
    if not business_is_network_eligible(other):
        raise TradingError("این همکار در حال حاضر امکان انجام معامله در سنگا را ندارد.")
    try:
        require_operational(seller_business)
        require_operational(buyer_business)
    except NotOperationalError as exc:
        raise TradingError(exc.message) from exc
    _require_plan(seller_business, FINALIZE_SALES)


def _clean_proposal_lines(
    lines: list[dict],
    *,
    seller_business: Business,
    membership: BusinessMembership,
) -> list[dict]:
    cleaned: list[dict] = []
    for line in lines or []:
        if not line:
            continue
        item = line.get("item")
        if item is not None and membership.business_id != seller_business.id:
            visible = get_eligible_item(
                audience="colleague",
                viewer_business=membership.business,
                item_id=item.pk,
            )
            if visible is None or visible.business_id != seller_business.id:
                raise TradingError("محصول انتخاب‌شده دیگر برای این معامله در دسترس نیست.")
            line = {**line, "item": visible}
        cleaned.append(_clean_line(line, seller_business=seller_business))
    if not cleaned:
        raise TradingError("حداقل یک محصول به توافق اضافه کنید.")
    return cleaned


@transaction.atomic
def save_trade_proposal(
    *,
    seller_business: Business,
    buyer_business: Business,
    membership: BusinessMembership,
    lines: list[dict],
    note: str = "",
    submission_id=None,
    submit: bool = True,
    proposal: TradeProposal | None = None,
) -> TradeProposal:
    """Create or edit a proposal without touching invoices or either ledger."""
    _require(membership, TRADE_PROPOSE)
    _validate_proposal_parties(
        seller_business=seller_business,
        buyer_business=buyer_business,
        membership=membership,
    )
    cleaned = _clean_proposal_lines(
        lines,
        seller_business=seller_business,
        membership=membership,
    )
    total = sum((line["line_total"] for line in cleaned), Decimal("0")).quantize(Decimal("0.01"))

    if proposal is not None:
        locked = TradeProposal.objects.select_for_update().get(pk=proposal.pk)
        if locked.initiated_by_business_id != membership.business_id:
            raise TradingError("فقط ثبت‌کننده توافق می‌تواند پیش‌نویس را ویرایش کند.")
        if locked.status != TradeProposal.Status.DRAFT:
            raise TradingError("فقط پیش‌نویس قابل ویرایش است.")
        proposal = locked
    else:
        if submission_id is not None:
            # Serialize retries from this initiator before the existence check;
            # the partial unique constraint remains the final invariant.
            Business.objects.select_for_update().get(pk=membership.business_id)
            existing = TradeProposal.objects.filter(
                initiated_by_business=membership.business,
                submission_id=submission_id,
            ).first()
            if existing is not None:
                return existing
        proposal = TradeProposal(
            initiated_by_business=membership.business,
            created_by=membership.user,
            submission_id=submission_id,
        )

    proposal.seller_business = seller_business
    proposal.buyer_business = buyer_business
    proposal.note = (note or "").strip()
    proposal.total_amount = total
    proposal.status = TradeProposal.Status.PENDING if submit else TradeProposal.Status.DRAFT
    proposal.submitted_at = timezone.now() if submit else None
    proposal.save()
    proposal.items.all().delete()
    _write_proposal_lines(proposal, cleaned)

    if submit:
        counterparty = proposal.other_business(membership.business)
        _notify_business(
            counterparty,
            capability=TRADE_CONFIRM,
            title="توافق معامله جدید برای تأیید",
            body=(
                f"{membership.business.name} جزئیات معامله‌ای به مبلغ "
                f"{proposal.total_amount:,.0f} ریال را برای تأیید شما ثبت کرد."
            ),
            link=f"/app/trading/agreements/{proposal.id}/",
        )
    return proposal


@transaction.atomic
def submit_trade_proposal(*, proposal: TradeProposal, membership: BusinessMembership) -> TradeProposal:
    _require(membership, TRADE_PROPOSE)
    locked = TradeProposal.objects.select_for_update().get(pk=proposal.pk)
    if locked.initiated_by_business_id != membership.business_id:
        raise TradingError("فقط ثبت‌کننده توافق می‌تواند آن را ارسال کند.")
    if locked.status != TradeProposal.Status.DRAFT:
        raise TradingError("این توافق قبلاً ارسال یا بسته شده است.")
    if not locked.items.exists():
        raise TradingError("توافق بدون محصول قابل ارسال نیست.")
    _validate_proposal_parties(
        seller_business=locked.seller_business,
        buyer_business=locked.buyer_business,
        membership=membership,
    )
    locked.status = TradeProposal.Status.PENDING
    locked.submitted_at = timezone.now()
    locked.save(update_fields=["status", "submitted_at", "updated_at"])
    counterparty = locked.other_business(membership.business)
    _notify_business(
        counterparty,
        capability=TRADE_CONFIRM,
        title="توافق معامله جدید برای تأیید",
        body=f"{membership.business.name} یک توافق معامله برای تأیید شما ثبت کرد.",
        link=f"/app/trading/agreements/{locked.id}/",
    )
    return locked


@transaction.atomic
def cancel_trade_proposal(*, proposal: TradeProposal, membership: BusinessMembership) -> TradeProposal:
    _require(membership, TRADE_PROPOSE)
    locked = TradeProposal.objects.select_for_update().get(pk=proposal.pk)
    if locked.initiated_by_business_id != membership.business_id:
        raise TradingError("فقط ثبت‌کننده توافق می‌تواند آن را لغو کند.")
    if locked.status not in TradeProposal.OPEN_STATUSES:
        raise TradingError("این توافق دیگر قابل لغو نیست.")
    locked.status = TradeProposal.Status.CANCELLED
    locked.decided_at = timezone.now()
    locked.save(update_fields=["status", "decided_at", "updated_at"])
    return locked


@transaction.atomic
def reject_trade_proposal(*, proposal: TradeProposal, membership: BusinessMembership) -> TradeProposal:
    _require(membership, TRADE_CONFIRM)
    locked = TradeProposal.objects.select_for_update().get(pk=proposal.pk)
    if locked.status != TradeProposal.Status.PENDING:
        raise TradingError("این توافق دیگر در انتظار پاسخ نیست.")
    if membership.business_id == locked.initiated_by_business_id:
        raise TradingError("ثبت‌کننده توافق نمی‌تواند پاسخ طرف مقابل را ثبت کند.")
    if membership.business_id not in {locked.seller_business_id, locked.buyer_business_id}:
        raise TradingError("شما طرف این توافق نیستید.")
    locked.status = TradeProposal.Status.REJECTED
    locked.decided_at = timezone.now()
    locked.save(update_fields=["status", "decided_at", "updated_at"])
    _notify_business(
        locked.initiated_by_business,
        capability=TRADE_PROPOSE,
        title="توافق معامله رد شد",
        body=f"{membership.business.name} توافق معامله را رد کرد.",
        link=f"/app/trading/agreements/{locked.id}/",
    )
    return locked


@transaction.atomic
def confirm_trade_proposal(*, proposal: TradeProposal, membership: BusinessMembership) -> Trade:
    """Atomically confirm once, then create the Trade, books and issued invoice."""
    _require(membership, TRADE_CONFIRM)
    locked = (
        # Lock only the proposal row. ``trade`` is nullable until confirmation,
        # so a bare FOR UPDATE across its LEFT JOIN is rejected by PostgreSQL.
        TradeProposal.objects.select_for_update(of=("self",))
        # Do not join/cache the nullable trade here. A concurrent confirmer may
        # populate trade_id while this statement waits for the proposal lock;
        # resolving ``locked.trade`` afterwards must observe that committed row.
        .select_related("seller_business", "buyer_business", "initiated_by_business")
        .prefetch_related("items")
        .get(pk=proposal.pk)
    )
    if locked.status == TradeProposal.Status.CONFIRMED and locked.trade_id:
        return locked.trade
    if locked.status != TradeProposal.Status.PENDING:
        raise TradingError("این توافق دیگر در انتظار تأیید نیست.")
    if membership.business_id == locked.initiated_by_business_id:
        raise TradingError("تأیید باید توسط طرف مقابل انجام شود.")
    if membership.business_id not in {locked.seller_business_id, locked.buyer_business_id}:
        raise TradingError("شما طرف این توافق نیستید.")

    _validate_proposal_parties(
        seller_business=locked.seller_business,
        buyer_business=locked.buyer_business,
        membership=membership,
    )
    proposal_lines = list(locked.items.all())
    if not proposal_lines:
        raise TradingError("توافق بدون محصول قابل تأیید نیست.")
    lines = [
        {
            "item": line.item,
            "product_name": line.product_name,
            "stone_type": line.stone_type,
            "grade": line.grade,
            "quantity": line.quantity,
            "unit_price": line.unit_price,
            "line_total": line.line_total,
        }
        for line in proposal_lines
    ]

    trade = Trade.objects.create(
        seller_business=locked.seller_business,
        counterparty_type=Trade.Counterparty.BUSINESS,
        buyer_business=locked.buyer_business,
        total_amount=locked.total_amount,
        currency=locked.currency,
        note=locked.note,
        finalized_at=timezone.now(),
        created_by=membership.user,
        **_header_snapshot(lines),
    )
    _write_lines(trade, lines)
    locked.status = TradeProposal.Status.CONFIRMED
    locked.trade = trade
    locked.confirmed_by = membership.user
    locked.decided_at = timezone.now()
    locked.save(update_fields=["status", "trade", "confirmed_by", "decided_at", "updated_at"])

    post_trade_entries(trade=trade, membership=membership)
    create_invoice_for_confirmed_trade(trade=trade, membership=membership, notes=locked.note)

    _notify_business(
        locked.initiated_by_business,
        capability=TRADE_PROPOSE,
        title="توافق معامله تأیید و نهایی شد",
        body=f"{membership.business.name} معامله را تأیید کرد؛ فاکتور و حساب دفتری ثبت شدند.",
        link=f"/app/trading/agreements/{locked.id}/",
    )
    logger.info("Trade proposal confirmed proposal=%s trade=%s", locked.id, trade.id)
    return trade


#: What to tell someone acting on a request that has already finished. Keyed by
#: the status found under the lock, because that is the one that is true.
_TERMINAL_REFUSALS: dict[str, str] = {
    PurchaseRequest.Status.COMPLETED: "این فروش قبلاً نهایی شده است.",
    PurchaseRequest.Status.REJECTED: "این درخواست رد شده است.",
    PurchaseRequest.Status.CANCELLED: "این درخواست لغو شده است.",
}

#: Ending here would contradict a sale that has already been recorded.
_ENDINGS_THAT_UNDO_A_SALE = frozenset(
    {PurchaseRequest.Status.CANCELLED, PurchaseRequest.Status.REJECTED}
)


def _lock_for_transition(request: PurchaseRequest, *, to: str, refusal: str) -> PurchaseRequest:
    """Re-read the request under a row lock, then decide whether ``to`` is legal.

    Both halves matter. Validating the caller's in-memory instance decides
    against a status that may have changed since the page rendered; validating
    without the lock lets two connections each read ``ACCEPTED`` and each write a
    different terminal status. The pairing that used to be missing produced the
    worst outcome available: a CANCELLED request owning a Trade, a ledger pair
    and an invoice — one commercial event described two contradictory ways.

    The transition itself is checked against
    :attr:`PurchaseRequest.ALLOWED_TRANSITIONS` rather than against whatever
    condition each caller happened to write, so a status can only ever move the
    way the product says it may.

    There is no database constraint behind this. "A request with a Trade is not
    cancelled" spans two tables, which PostgreSQL cannot express as a CHECK, and
    a trigger would hide a commercial rule where nobody reading this module would
    find it. The row lock is the enforcement; the concurrency tests are the
    proof.
    """
    locked = (
        PurchaseRequest.objects.select_for_update()
        .select_related("seller_business", "buyer_business", "item", "item__product")
        .get(pk=request.pk)
    )

    if to not in PurchaseRequest.ALLOWED_TRANSITIONS.get(locked.status, frozenset()):
        raise TradingError(_TERMINAL_REFUSALS.get(locked.status, refusal))

    if to in _ENDINGS_THAT_UNDO_A_SALE and Trade.objects.filter(purchase_request=locked).exists():
        raise TradingError("این درخواست به فروش نهایی رسیده و دیگر قابل لغو یا رد نیست.")

    return locked


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
        if price <= 0:
            raise TradingError("قیمت پیشنهادی باید بزرگ‌تر از صفر باشد.")

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
        # Whoever can respond to it. Answering a request is the work this
        # notification exists to prompt.
        capability=PURCHASE_REQUEST,
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

    locked = _lock_for_transition(
        request,
        to=PurchaseRequest.Status.CANCELLED,
        refusal="این درخواست دیگر قابل لغو نیست.",
    )
    if locked.buyer_business_id != membership.business_id:
        raise TradingError("این درخواست متعلق به کسب‌وکار شما نیست.")

    locked.status = PurchaseRequest.Status.CANCELLED
    locked.decided_at = timezone.now()
    locked.save(update_fields=["status", "decided_at", "updated_at"])
    return locked


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

    decision = PurchaseRequest.Status.ACCEPTED if accept else PurchaseRequest.Status.REJECTED
    locked = _lock_for_transition(
        request,
        to=decision,
        refusal="به این درخواست قبلاً پاسخ داده شده است.",
    )
    if locked.seller_business_id != membership.business_id:
        raise TradingError("این درخواست متعلق به کسب‌وکار شما نیست.")

    locked.seller_note = (seller_note or "").strip()
    locked.decided_at = timezone.now()

    if not accept:
        locked.status = PurchaseRequest.Status.REJECTED
        locked.save(update_fields=["status", "seller_note", "decided_at", "updated_at"])
        _notify_business(
            locked.buyer_business,
            capability=PURCHASE_REQUEST,
            title="درخواست خرید رد شد",
            body=f"{locked.seller_business.name} درخواست شما را نپذیرفت.",
            link=f"/app/trading/sent/{locked.id}/",
        )
        return locked

    if final_qty_sqm not in (None, ""):
        qty = _quantize(final_qty_sqm)
        if qty <= 0:
            raise TradingError("متراژ نهایی باید بزرگ‌تر از صفر باشد.")
        locked.final_qty_sqm = qty
    if final_unit_price not in (None, ""):
        price = _quantize(final_unit_price, "0.01")
        if price <= 0:
            raise TradingError("قیمت نهایی باید بزرگ‌تر از صفر باشد.")
        locked.final_unit_price = price

    if locked.agreed_unit_price is None:
        raise TradingError("برای پذیرش درخواست، قیمت نهایی را وارد کنید.")

    locked.status = PurchaseRequest.Status.ACCEPTED
    locked.save(
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
        locked.buyer_business,
        capability=PURCHASE_REQUEST,
        title="درخواست خرید پذیرفته شد",
        body=f"{locked.seller_business.name} با درخواست شما موافقت کرد.",
        link=f"/app/trading/sent/{locked.id}/",
    )
    return locked


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
    locked = _lock_for_transition(
        request,
        to=PurchaseRequest.Status.COMPLETED,
        refusal="فقط درخواست‌های پذیرفته‌شده قابل نهایی شدن هستند.",
    )
    if locked.seller_business_id != membership.business_id:
        raise TradingError("این درخواست متعلق به کسب‌وکار شما نیست.")
    # Read the plan from the freshly-loaded row rather than from whatever the
    # caller had in memory: a subscription that lapsed since the page rendered
    # must block the sale.
    _require_plan(locked.seller_business, FINALIZE_SALES)

    unit_price = locked.agreed_unit_price
    if unit_price is None:
        raise TradingError("قیمت نهایی مشخص نیست.")

    # A purchase request references exactly one product, so its trade has
    # exactly one line. Multi-line sales come in through record_direct_sale.
    lines = [
        _clean_line(
            {
                "item": locked.item,
                "quantity": locked.agreed_qty_sqm,
                "unit_price": unit_price,
            },
            seller_business=locked.seller_business,
        )
    ]
    total = sum((line["line_total"] for line in lines), Decimal("0")).quantize(Decimal("0.01"))

    trade = Trade.objects.create(
        seller_business=locked.seller_business,
        counterparty_type=Trade.Counterparty.BUSINESS,
        buyer_business=locked.buyer_business,
        purchase_request=locked,
        total_amount=total,
        currency=locked.currency,
        note=(note or "").strip(),
        finalized_at=timezone.now(),
        created_by=membership.user,
        **_header_snapshot(lines),
    )
    _write_lines(trade, lines)

    locked.status = PurchaseRequest.Status.COMPLETED
    locked.save(update_fields=["status", "updated_at"])

    # One transaction covers: create Trade → post both books → link an invoice.
    # If the ledger post fails, the whole finalization rolls back rather than
    # leaving a sale that never reached the books.
    post_trade_entries(trade=trade, membership=membership)
    safe_create_invoice_for_trade(trade=trade, membership=membership)

    _notify_business(
        locked.buyer_business,
        # The buyer's side of a finalized sale: their account has just moved, so
        # this goes to whoever tracks what they have bought.
        capability=PURCHASE_REQUEST,
        title="فروش نهایی شد",
        body=f"{locked.seller_business.name} فروش «{trade.summary_label}» را نهایی کرد.",
        link=f"/app/trading/sent/{locked.id}/",
    )
    logger.info("Trade finalized id=%s request=%s total=%s", trade.id, locked.id, total)
    return trade


@transaction.atomic
def record_direct_sale(
    *,
    seller_business: Business,
    membership: BusinessMembership,
    lines: list[dict] | None = None,
    buyer_business: Business | None = None,
    customer_name: str = "",
    customer_phone: str = "",
    note: str = "",
    submission_id=None,
    # Single-line shorthand, kept because most sales are one product and making
    # every caller build a list to say so would be noise.
    item: InventoryLot | None = None,
    quantity_sqm=None,
    unit_price=None,
    product_name: str = "",
) -> Trade:
    """Record a sale that did not come through a purchase request.

    Most sales still happen over the phone. Forcing the seller to invent a
    request first would make them stop recording sales at all — and forcing them
    to record a three-stone order as three sales would do the same.

    One call is one commercial event however many lines it carries: one Trade,
    one total, one entry in each party's book, one invoice.

    Idempotent on ``submission_id`` three ways, because none of them is
    sufficient alone: the seller's row is locked *before* the lookup so
    concurrent callers serialize, the lookup runs again under that lock, and
    ``uniq_trade_per_submission`` catches anything that still slips past — in
    which case the loser is handed the winner's Trade rather than an error. The
    ledger and the invoice hang off the returned Trade, so one submission moves
    each book once and produces one document however many times it arrives.
    """
    _require(membership, SALE_FINALIZE)
    if membership.business_id != seller_business.id:
        raise TradingError("دسترسی نامعتبر است.")
    _require_plan(seller_business, FINALIZE_SALES)

    if lines is None:
        lines = [
            {
                "item": item,
                "product_name": product_name,
                "quantity": quantity_sqm,
                "unit_price": unit_price,
            }
        ]
    cleaned = [_clean_line(line, seller_business=seller_business) for line in lines if line]
    if not cleaned:
        raise TradingError("حداقل یک ردیف به این فروش اضافه کنید.")
    total = sum((line["line_total"] for line in cleaned), Decimal("0")).quantize(Decimal("0.01"))

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

    if submission_id is not None:
        # Lock first, then look. Checking before the lock is what lets two
        # requests both conclude "no sale yet" and both record one.
        Business.objects.select_for_update().get(pk=seller_business.pk)
        existing = Trade.objects.filter(
            seller_business=seller_business, submission_id=submission_id
        ).first()
        if existing is not None:
            logger.info("Direct sale submission %s already recorded as trade %s", submission_id, existing.id)
            # Still ask for the invoice. Invoicing is best-effort — a lapsed
            # entitlement or a transient failure leaves a finalized sale with no
            # document — so a retry is the natural moment to heal that. It cannot
            # duplicate anything: create_invoice_for_trade returns the existing
            # invoice when there is one. The ledger needs no such treatment,
            # because it is posted inside this transaction and a failure there
            # rolls the whole sale back rather than leaving a trade behind.
            safe_create_invoice_for_trade(trade=existing, membership=membership)
            return existing

    try:
        # Savepoint so a constraint violation leaves the surrounding transaction
        # usable instead of poisoning the whole sale.
        with transaction.atomic():
            trade = Trade.objects.create(
                seller_business=seller_business,
                submission_id=submission_id,
                counterparty_type=counterparty_type,
                buyer_business=buyer_business,
                customer_name=customer_name,
                customer_phone=(customer_phone or "").strip(),
                total_amount=total,
                note=(note or "").strip(),
                finalized_at=timezone.now(),
                created_by=membership.user,
                **_header_snapshot(cleaned),
            )
            _write_lines(trade, cleaned)
    except IntegrityError:
        winner = (
            Trade.objects.filter(seller_business=seller_business, submission_id=submission_id).first()
            if submission_id is not None
            else None
        )
        if winner is None:
            raise
        logger.info("Concurrent direct sale for submission %s resolved to trade %s", submission_id, winner.id)
        safe_create_invoice_for_trade(trade=winner, membership=membership)
        return winner

    # A finalized sale is a finalized sale, however it was reached. Posting only
    # for request-driven sales would leave the books wrong for every deal agreed
    # over the phone — which is most of them. A walk-in customer has no account,
    # so post_trade_entries posts nothing for those.
    post_trade_entries(trade=trade, membership=membership)
    safe_create_invoice_for_trade(trade=trade, membership=membership)
    logger.info("Direct sale recorded trade=%s seller=%s total=%s", trade.id, seller_business.id, trade.total_amount)
    return trade
