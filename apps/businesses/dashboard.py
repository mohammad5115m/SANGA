"""Data for the business dashboard («داشبورد»).

One place that assembles everything the home screen shows, so the tenant scope
and the capability gate live in the data layer instead of in the template. The
view renders whatever this returns; hiding markup is never the gate.

Everything here is bounded: each list is sliced, each total is a database
aggregate, and nothing loops over rows to compute a number. The query count of
the whole page is pinned by a test.
"""

from __future__ import annotations

from django.db.models import Count, Exists, OuterRef, Q

from apps.accounting.selectors import (
    business_financial_summary,
    counterparty_balances,
    describe_balance,
)
from apps.inquiries.models import Inquiry
from apps.inventory.models import InventoryLot
from apps.marketplace.selectors import marketplace_lots_for
from apps.pricing.models import LotPrice
from apps.trading.models import PurchaseRequest

from .models import Business, BusinessMembership
from .permissions import LEDGER_VIEW

# Row limits. The dashboard is a starting point, not a report: every section
# links to the full screen.
TOP_BALANCE_ROWS = 5
ATTENTION_ROWS = 8
COLLEAGUE_LOT_ROWS = 6
PENDING_ROWS = 5

# «بی‌پاسخ»: nobody has replied yet. Once the inquiry is marked تماس گرفته‌شده
# somebody is on it, so it is no longer a task waiting on the team.
UNANSWERED_INQUIRY_STATUSES = (Inquiry.Status.NEW,)

ATTENTION_NEEDS_CONFIRMATION = "نیاز به تأیید موجودی"
ATTENTION_PRICE_EXPIRED = "قیمت نیاز به بررسی دارد"
ATTENTION_NO_PRICE = "بدون قیمت — قابل فروش نیست"


def dashboard_data(*, business: Business, membership: BusinessMembership | None) -> dict:
    """Everything the dashboard renders, already scoped to ``business``.

    The financial sections (خلاصه مالی and the top debtors/creditors) are
    computed **only** for a member holding ``ledger.view``; for anyone else the
    keys are present but empty, so the template renders a coherent dashboard
    without them instead of an empty frame. A caller can therefore not leak a
    balance by forgetting an ``{% if %}``.
    """
    can_view_ledger = membership is not None and membership.has_capability(LEDGER_VIEW)
    return {
        "can_view_ledger": can_view_ledger,
        "finance": business_financial_summary(business) if can_view_ledger else None,
        "top_debtors": _top_balances(business, "debtor") if can_view_ledger else [],
        "top_creditors": _top_balances(business, "creditor") if can_view_ledger else [],
        **_lots_needing_attention(business),
        "colleague_lots": _colleague_lots(business),
        **_pending_work(business),
        **_recent_activity(business),
    }


def _recent_activity(business: Business) -> dict:
    """The last few sales and invoices, so the home screen shows movement.

    Operational, not analytical: a seller opening SANGA wants to see what needs
    doing and what just happened, not a chart.
    """
    from apps.invoicing.models import SalesInvoice
    from apps.trading.models import Trade

    return {
        "recent_trades": list(
            Trade.objects.filter(seller_business=business)
            .select_related("buyer_business")
            .order_by("-finalized_at")[:PENDING_ROWS]
        ),
        "recent_invoices": list(
            SalesInvoice.objects.filter(seller_business=business).order_by("-issue_date", "-created_at")[
                :PENDING_ROWS
            ]
        ),
    }


def _top_balances(business: Business, state: str) -> list[dict]:
    """The few largest balances on one side of the books, largest first.

    ``counterparty_balances`` already sums and sorts in the database, so this
    only labels the rows.
    """
    rows = counterparty_balances(business, state=state, sort=state)[:TOP_BALANCE_ROWS]
    return [{"colleague": row, "balance": describe_balance(row.balance)} for row in rows]


def _lots_needing_attention(business: Business) -> dict:
    """Own lots the owner has to act on, as **one** list with a reason per row.

    Two separate lists would split one errand — «کدام محصولات ایراد دارند؟» —
    across two panels that mostly hold the same lots, and on a phone the second
    one falls below the fold. A single list ordered by recency with a reason
    badge answers it in one scan, and a lot that is both unconfirmed and unpriced
    appears once with both reasons instead of twice.

    ``has_price`` is an ``EXISTS`` sub-query, so the reasons cost no query per
    lot, and the totals are one aggregate over the same queryset.
    """
    needs_stock = InventoryLot.needs_stock_confirmation_q()
    stale_price = Exists(LotPrice.objects.filter(lot=OuterRef("pk")).filter(LotPrice.needs_confirmation_q()))

    sellable = (
        InventoryLot.objects.filter(
            business=business,
            deleted_at__isnull=True,
            status=InventoryLot.Status.ACTIVE,
            availability_status=InventoryLot.Availability.AVAILABLE,
        )
        .annotate(
            has_price=Exists(LotPrice.objects.filter(lot=OuterRef("pk"))),
            has_stale_price=stale_price,
        )
        .annotate(stock_stale=Q(needs_stock))
    )
    totals = sellable.aggregate(
        active=Count("pk"),
        needs_confirmation=Count("pk", filter=needs_stock),
        stale_price=Count("pk", filter=Q(has_stale_price=True)),
        no_price=Count("pk", filter=Q(has_price=False)),
    )
    lots = (
        sellable.filter(needs_stock | Q(has_price=False) | Q(has_stale_price=True))
        .select_related("product")
        .order_by("-updated_at")[:ATTENTION_ROWS]
    )
    return {
        "lot_totals": totals,
        "attention_lots": [{"lot": lot, "reasons": _attention_reasons(lot)} for lot in lots],
    }


def _attention_reasons(lot: InventoryLot) -> list[str]:
    """Why this item is on the list. Reads only annotated/loaded fields."""
    reasons: list[str] = []
    if lot.stock_stale:
        reasons.append(ATTENTION_NEEDS_CONFIRMATION)
    if not lot.has_price:
        reasons.append(ATTENTION_NO_PRICE)
    elif lot.has_stale_price:
        reasons.append(ATTENTION_PRICE_EXPIRED)
    return reasons


def _colleague_lots(business: Business):
    """The newest lots from other businesses, straight through the marketplace
    selector.

    Deliberately not a query of its own: reusing ``marketplace_lots_for``
    inherits the visibility rules, the "not my own lots" rule and the
    active-business gate, so this panel cannot drift into a leak. Only the
    prefetches are dropped — the dashboard shows no prices and no photos, so
    loading them would be three queries for data that is never rendered.
    """
    return (
        marketplace_lots_for(business)
        .prefetch_related(None)
        .order_by("-created_at")[:COLLEAGUE_LOT_ROWS]
    )


def _pending_work(business: Business) -> dict:
    """Work waiting on **this** business.

    Two queues, both of which are somebody else waiting for an answer from us:
    customer inquiries nobody has replied to, and colleague purchase requests
    nobody has accepted or rejected.

    Requests this business *sent* are not here — they are waiting on somebody
    else, so they are not a task on this screen. Accepted requests are, though:
    an agreement that has not been finalized is unfinished work, and forgetting
    it is exactly the failure the accept/finalize split creates.
    """
    inquiries = (
        Inquiry.objects.filter(business=business, status__in=UNANSWERED_INQUIRY_STATUSES)
        .select_related("lot", "lot__product")
        .order_by("-created_at")
    )
    requests = (
        PurchaseRequest.objects.filter(
            seller_business=business,
            status__in=[PurchaseRequest.Status.SENT, PurchaseRequest.Status.ACCEPTED],
        )
        .select_related("buyer_business", "item", "item__product")
        .order_by("-created_at")
    )
    return {
        "unanswered_inquiry_count": inquiries.count(),
        "unanswered_inquiries": list(inquiries[:PENDING_ROWS]),
        "open_request_count": requests.count(),
        "open_requests": list(requests[:PENDING_ROWS]),
        "awaiting_finalize_count": requests.filter(status=PurchaseRequest.Status.ACCEPTED).count(),
    }
