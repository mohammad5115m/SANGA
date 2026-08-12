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
    contact_balances,
    describe_balance,
)
from apps.inquiries.models import Inquiry
from apps.inventory.models import InventoryLot
from apps.marketplace.selectors import marketplace_lots_for
from apps.pricing.models import LotPrice
from apps.purchase_requests.models import PurchaseOffer

from .models import Business, BusinessMembership
from .permissions import LEDGER_VIEW

# Row limits. The dashboard is a starting point, not a report: every section
# links to the full screen.
TOP_BALANCE_ROWS = 5
ATTENTION_ROWS = 8
COLLEAGUE_LOT_ROWS = 6
PENDING_ROWS = 5

# Lot states that are neither sellable nor worth nagging about: sold and expired
# are done, a draft is unfinished by choice, and a hidden lot was hidden on
# purpose.
IDLE_LOT_STATUSES = (
    InventoryLot.Status.SOLD,
    InventoryLot.Status.DRAFT,
    InventoryLot.Status.HIDDEN,
    InventoryLot.Status.EXPIRED,
)

# «بی‌پاسخ»: nobody has replied yet. Once the inquiry is marked تماس گرفته‌شده or
# در حال مذاکره someone is on it, so it is no longer a task waiting on the team.
UNANSWERED_INQUIRY_STATUSES = (Inquiry.Status.NEW, Inquiry.Status.VIEWED)

ATTENTION_NEEDS_CONFIRMATION = "نیاز به تأیید موجودی"
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
    }


def _top_balances(business: Business, state: str) -> list[dict]:
    """The few largest balances on one side of the books, largest first.

    ``contact_balances`` already sums and sorts in the database and already
    decides which archived contacts still count, so this only labels the rows.
    """
    rows = contact_balances(business, state=state, sort=state)[:TOP_BALANCE_ROWS]
    return [{"contact": contact, "balance": describe_balance(contact.balance)} for contact in rows]


def _lots_needing_attention(business: Business) -> dict:
    """Own lots the owner has to act on, as **one** list with a reason per row.

    Two separate lists would split one errand — «کدام محموله‌ها ایراد دارند؟» —
    across two panels that mostly hold the same lots, and on a phone the second
    one falls below the fold. A single list ordered by recency with a reason
    badge answers it in one scan, and a lot that is both unconfirmed and unpriced
    appears once with both reasons instead of twice.

    ``has_price`` is an ``EXISTS`` sub-query, so the reasons cost no query per
    lot, and the totals are one aggregate over the same queryset.
    """
    sellable = (
        InventoryLot.objects.filter(business=business, archived_at__isnull=True)
        .exclude(status__in=IDLE_LOT_STATUSES)
        .annotate(has_price=Exists(LotPrice.objects.filter(lot=OuterRef("pk"))))
    )
    totals = sellable.aggregate(
        active=Count("pk"),
        needs_confirmation=Count(
            "pk", filter=Q(status=InventoryLot.Status.NEEDS_CONFIRMATION)
        ),
        no_price=Count("pk", filter=Q(has_price=False)),
    )
    lots = (
        sellable.filter(
            Q(status=InventoryLot.Status.NEEDS_CONFIRMATION) | Q(has_price=False)
        )
        .select_related("product")
        .order_by("-updated_at")[:ATTENTION_ROWS]
    )
    return {
        "lot_totals": totals,
        "attention_lots": [
            {"lot": lot, "reasons": _attention_reasons(lot)} for lot in lots
        ],
    }


def _attention_reasons(lot: InventoryLot) -> list[str]:
    """Why this lot is on the list. Reads only annotated/loaded fields."""
    reasons: list[str] = []
    if lot.status == InventoryLot.Status.NEEDS_CONFIRMATION:
        reasons.append(ATTENTION_NEEDS_CONFIRMATION)
    if not lot.has_price:
        reasons.append(ATTENTION_NO_PRICE)
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
    """Requests waiting on **this** business: unanswered inquiries, and offers
    received on its own purchase requests that it has not yet accepted or
    rejected.

    Offers this business *sent* are not here: they are waiting on somebody else,
    so they are not a task on this screen.
    """
    inquiries = (
        Inquiry.objects.filter(business=business, status__in=UNANSWERED_INQUIRY_STATUSES)
        .select_related("lot", "lot__product")
        .order_by("-created_at")
    )
    offers = (
        PurchaseOffer.objects.filter(
            purchase_request__business=business,
            status=PurchaseOffer.Status.SUBMITTED,
        )
        .select_related("purchase_request", "seller_business")
        .order_by("-created_at")
    )
    return {
        "unanswered_inquiry_count": inquiries.count(),
        "unanswered_inquiries": list(inquiries[:PENDING_ROWS]),
        "unanswered_offer_count": offers.count(),
        "unanswered_offers": list(offers[:PENDING_ROWS]),
    }
