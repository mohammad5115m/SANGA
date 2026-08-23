"""Operational reports.

Tables, totals, filters and a print view. Not a BI platform: no charts, no
dashboards-of-dashboards, no warehouse. A stone trader wants to know who owes
them money and what sold last month, and both are one query.

Every report is a database aggregate over a date-bounded queryset. Nothing loops
over rows in Python to compute a total, so a business with ten thousand trades
costs the same as one with ten.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import Count, DecimalField, Q, QuerySet, Sum, Value
from django.db.models.functions import Coalesce

from apps.accounting.models import LedgerEntry
from apps.accounting.selectors import describe_balance
from apps.businesses.models import Business
from apps.inventory.models import InventoryLot
from apps.invoicing.models import ChequeReceivable, SalesInvoice
from apps.pricing.models import LotPrice
from apps.trading.models import Trade, TradeItem

ZERO = Decimal("0.00")
MONEY = DecimalField(max_digits=18, decimal_places=2)
QUANTITY = DecimalField(max_digits=16, decimal_places=3)


@dataclass(frozen=True)
class DateRange:
    """An inclusive from/to window. Both ends optional."""

    date_from: date | None = None
    date_to: date | None = None

    @property
    def is_bounded(self) -> bool:
        return self.date_from is not None or self.date_to is not None

    @property
    def label(self) -> str:
        if self.date_from and self.date_to:
            return f"{self.date_from} تا {self.date_to}"
        if self.date_from:
            return f"از {self.date_from}"
        if self.date_to:
            return f"تا {self.date_to}"
        return "همه تاریخ‌ها"

    def apply(self, qs: QuerySet, field: str) -> QuerySet:
        """Narrow ``qs`` on a **date** field. Datetime fields use :func:`apply_dt`."""
        if self.date_from:
            qs = qs.filter(**{f"{field}__gte": self.date_from})
        if self.date_to:
            qs = qs.filter(**{f"{field}__lte": self.date_to})
        return qs

    def apply_dt(self, qs: QuerySet, field: str) -> QuerySet:
        """Narrow ``qs`` on a timestamp, comparing dates.

        Separate from :meth:`apply` because comparing a date to a datetime drops
        everything recorded later on the closing day — an off-by-one that silently
        understates the last day of every report.
        """
        if self.date_from:
            qs = qs.filter(**{f"{field}__date__gte": self.date_from})
        if self.date_to:
            qs = qs.filter(**{f"{field}__date__lte": self.date_to})
        return qs


def _trades(business: Business, window: DateRange) -> QuerySet[Trade]:
    return window.apply_dt(Trade.objects.filter(seller_business=business), "finalized_at")


def _lines(business: Business, window: DateRange) -> QuerySet[TradeItem]:
    """The sold lines, for reports that break a sale down by what was in it.

    Money is never summed across this queryset joined to ``Trade``: a two-line
    sale would contribute its total twice. Reports that need both take the money
    from the header and the breakdown from here.
    """
    return window.apply_dt(
        TradeItem.objects.filter(trade__seller_business=business), "trade__finalized_at"
    )


def _money(value) -> Decimal:
    return Decimal(value or 0).quantize(Decimal("0.01"))


# --- 1. sales by colleague ------------------------------------------------------

#: What identifies one counterparty in a sales report: a colleague by id, or a
#: walk-in customer by the name they were recorded under.
_COUNTERPARTY_KEYS = ("buyer_business__id", "counterparty_type", "customer_name")


def _key(row: dict, *, prefix: str = "") -> tuple:
    return tuple(row[f"{prefix}{key}"] for key in _COUNTERPARTY_KEYS)


def sales_by_colleague(business: Business, window: DateRange) -> list[dict]:
    """Money and trade counts from the sale headers; metres from its lines.

    Two aggregates rather than one join, because summing ``total_amount`` over a
    join to the lines multiplies every multi-line sale by the number of stones in
    it. Both are grouped on the same key and merged here, so neither number is
    inflated.
    """
    quantities = {
        _key(row, prefix="trade__"): row["quantity"]
        for row in _lines(business, window)
        .values(*[f"trade__{key}" for key in _COUNTERPARTY_KEYS])
        .annotate(quantity=Coalesce(Sum("quantity"), Value(Decimal("0")), output_field=QUANTITY))
    }
    rows = (
        _trades(business, window)
        .values("buyer_business__id", "buyer_business__name", "counterparty_type", "customer_name")
        .annotate(
            total=Coalesce(Sum("total_amount"), Value(ZERO), output_field=MONEY),
            trade_count=Count("id"),
        )
        .order_by("-total")
    )
    return [
        {
            "name": row["buyer_business__name"] or row["customer_name"] or "مشتری",
            "business_id": row["buyer_business__id"],
            "is_colleague": row["counterparty_type"] == Trade.Counterparty.BUSINESS,
            "total": _money(row["total"]),
            "quantity": quantities.get(_key(row), Decimal("0")),
            "trade_count": row["trade_count"],
        }
        for row in rows
    ]


# --- 2. sales by stone type -----------------------------------------------------


def sales_by_stone_type(business: Business, window: DateRange) -> list[dict]:
    """Grouped on each sold line's own snapshot, not the live product.

    A product renamed or reclassified after the sale must not silently move
    historical revenue into a different category. Grouping happens on the lines
    because one sale can legitimately span three stone types, and attributing all
    of its revenue to whichever happened to be first would be wrong.
    """
    rows = (
        _lines(business, window)
        .values("stone_type")
        .annotate(
            total=Coalesce(Sum("line_total"), Value(ZERO), output_field=MONEY),
            quantity=Coalesce(Sum("quantity"), Value(Decimal("0")), output_field=QUANTITY),
            trade_count=Count("trade", distinct=True),
        )
        .order_by("-total")
    )
    return [
        {
            "name": row["stone_type"] or "نامشخص",
            "total": _money(row["total"]),
            "quantity": row["quantity"],
            "trade_count": row["trade_count"],
        }
        for row in rows
    ]


# --- 3. sales by product --------------------------------------------------------


def sales_by_product(business: Business, window: DateRange) -> list[dict]:
    rows = (
        _lines(business, window)
        .values("product_name")
        .annotate(
            total=Coalesce(Sum("line_total"), Value(ZERO), output_field=MONEY),
            quantity=Coalesce(Sum("quantity"), Value(Decimal("0")), output_field=QUANTITY),
            trade_count=Count("trade", distinct=True),
        )
        .order_by("-total")
    )
    return [
        {
            "name": row["product_name"],
            "total": _money(row["total"]),
            "quantity": row["quantity"],
            "trade_count": row["trade_count"],
        }
        for row in rows
    ]


# --- 4 + 9. totals over a date range --------------------------------------------


def sales_summary(business: Business, window: DateRange) -> dict:
    """Headline numbers: total sold, total square metres, trade count.

    The money and the count come from the sale headers so a multi-line sale is
    one sale for one amount; the metres come from its lines, which is the only
    place they exist once a sale covers several stones.
    """
    agg = _trades(business, window).aggregate(
        total=Coalesce(Sum("total_amount"), Value(ZERO), output_field=MONEY),
        trade_count=Count("id"),
    )
    metres = _lines(business, window).aggregate(
        quantity=Coalesce(Sum("quantity"), Value(Decimal("0")), output_field=QUANTITY),
    )
    return {
        "total": _money(agg["total"]),
        "quantity_sqm": metres["quantity"] or Decimal("0"),
        "trade_count": agg["trade_count"],
    }


def money_movement(business: Business, window: DateRange) -> dict:
    """What was received and paid in the window, from the ledger.

    Reversed entries and reversals themselves are excluded, so a corrected
    receipt does not appear as money that arrived.
    """
    qs = LedgerEntry.objects.filter(business=business, reversed_at__isnull=True).exclude(
        entry_type=LedgerEntry.Type.REVERSAL
    )
    qs = window.apply(qs, "occurred_on")
    agg = qs.aggregate(
        received=Coalesce(
            Sum("amount", filter=Q(entry_type=LedgerEntry.Type.PAYMENT_RECEIVED)),
            Value(ZERO),
            output_field=MONEY,
        ),
        paid=Coalesce(
            Sum("amount", filter=Q(entry_type=LedgerEntry.Type.PAYMENT_MADE)),
            Value(ZERO),
            output_field=MONEY,
        ),
        sold=Coalesce(
            Sum("amount", filter=Q(entry_type=LedgerEntry.Type.SALE)),
            Value(ZERO),
            output_field=MONEY,
        ),
        purchased=Coalesce(
            Sum("amount", filter=Q(entry_type=LedgerEntry.Type.PURCHASE)),
            Value(ZERO),
            output_field=MONEY,
        ),
    )
    return {key: _money(value) for key, value in agg.items()}


# --- 5 + 6. debtors and creditors -----------------------------------------------


def balances(business: Business, *, state: str) -> list[dict]:
    """Colleagues who owe us («بدهکار») or whom we owe («بستانکار»)."""
    from apps.accounting.selectors import counterparty_balances

    rows = counterparty_balances(business, state=state, sort=state)
    return [
        {"colleague": colleague, "balance": describe_balance(colleague.balance)}
        for colleague in rows
    ]


# --- 10. products needing a stock check -----------------------------------------


def stock_needing_confirmation(business: Business) -> QuerySet[InventoryLot]:
    """Published products showing a quantity the seller no longer vouches for."""
    return (
        InventoryLot.objects.filter(
            business=business,
            deleted_at__isnull=True,
            status=InventoryLot.Status.ACTIVE,
            availability_status=InventoryLot.Availability.AVAILABLE,
        )
        .filter(InventoryLot.needs_stock_confirmation_q())
        .select_related("product")
        .order_by("stock_expires_at")
    )


def prices_needing_confirmation(business: Business) -> QuerySet[LotPrice]:
    return (
        LotPrice.objects.filter(
            lot__business=business,
            lot__deleted_at__isnull=True,
            lot__status=InventoryLot.Status.ACTIVE,
        )
        .filter(LotPrice.needs_confirmation_q())
        .select_related("tier", "lot", "lot__product")
        .order_by("price_expires_at")
    )


# --- 8. invoices ------------------------------------------------------------------


def invoices_in_range(business: Business, window: DateRange) -> QuerySet[SalesInvoice]:
    visible_received = (
        SalesInvoice.Status.AWAITING_CONFIRMATION,
        SalesInvoice.Status.CONFIRMED,
        SalesInvoice.Status.ISSUED,
        SalesInvoice.Status.CANCELLED_BY_SENDER,
        SalesInvoice.Status.CANCELLED,
    )
    qs = SalesInvoice.objects.filter(
        Q(seller_business=business) | Q(buyer_business=business, status__in=visible_received)
    ).select_related("seller_business", "buyer_business", "local_counterparty")
    return window.apply(qs, "issue_date").order_by("-issue_date", "-created_at")


def invoice_summary(business: Business, window: DateRange) -> dict:
    """Invoice counts and money, with each number meaning one thing.

    ``total`` sums finalized outgoing invoices only. Registered-business invoices
    finalize as ``confirmed`` while customer and imported legacy documents use
    ``issued``. Drafts and pending documents never enter a financial total.

    Drafts and cancelled documents are still counted, and drafts get their own
    subtotal, so a voided or unfinished document is visible rather than missing —
    without any single number doing two jobs.
    """
    qs = invoices_in_range(business, window)
    finalized = (SalesInvoice.Status.ISSUED, SalesInvoice.Status.CONFIRMED)
    cancelled = (SalesInvoice.Status.CANCELLED, SalesInvoice.Status.CANCELLED_BY_SENDER)
    agg = qs.aggregate(
        issued_count=Count("id", filter=Q(status__in=finalized)),
        pending_count=Count("id", filter=Q(status=SalesInvoice.Status.AWAITING_CONFIRMATION)),
        draft_count=Count("id", filter=Q(status=SalesInvoice.Status.DRAFT)),
        cancelled_count=Count("id", filter=Q(status__in=cancelled)),
        total_count=Count("id"),
    )
    currency_labels = dict(SalesInvoice.Currency.choices)

    def totals_for(*, statuses: tuple[str, ...], direction: str = "") -> list[dict]:
        totals_qs = qs.filter(status__in=statuses)
        if direction == "sent":
            totals_qs = totals_qs.filter(seller_business=business)
        elif direction == "received":
            totals_qs = totals_qs.filter(buyer_business=business).exclude(seller_business=business)
        rows = (
            totals_qs
            .values("currency")
            .annotate(total=Coalesce(Sum("total_amount"), Value(ZERO), output_field=MONEY))
            .order_by("currency")
        )
        return [
            {
                "currency": row["currency"],
                "label": currency_labels.get(row["currency"], row["currency"]),
                "total": _money(row["total"]),
            }
            for row in rows
        ]

    issued_totals = totals_for(statuses=finalized, direction="sent")
    received_totals = totals_for(statuses=finalized, direction="received")
    draft_totals = totals_for(statuses=(SalesInvoice.Status.DRAFT,), direction="sent")

    def scalar_total(rows: list[dict]) -> Decimal | None:
        if not rows:
            return ZERO
        return rows[0]["total"] if len(rows) == 1 else None

    return {
        # Compatibility for existing one-currency consumers. Mixed currencies
        # deliberately have no scalar sum because it would be financially false.
        "total": scalar_total(issued_totals),
        "draft_total": scalar_total(draft_totals),
        "totals": issued_totals,
        "received_totals": received_totals,
        "draft_totals": draft_totals,
        "issued_count": agg["issued_count"],
        "pending_count": agg["pending_count"],
        "draft_count": agg["draft_count"],
        "cancelled_count": agg["cancelled_count"],
        "total_count": agg["total_count"],
    }


def cheques_in_range(business: Business, window: DateRange) -> QuerySet[ChequeReceivable]:
    """Cheque instruments attached to visible purchase or sale invoices."""
    qs = ChequeReceivable.objects.filter(
        Q(invoice__seller_business=business) | Q(invoice__buyer_business=business)
    ).select_related("invoice", "invoice__seller_business", "invoice__buyer_business")
    return window.apply(qs, "due_date").order_by("due_date", "reference_number")


def cheque_summary(business: Business, window: DateRange) -> dict:
    qs = cheques_in_range(business, window)
    counts = qs.aggregate(
        total_count=Count("id"),
        pending_count=Count(
            "id",
            filter=Q(status__in=(ChequeReceivable.Status.RECEIVED, ChequeReceivable.Status.IN_COLLECTION)),
        ),
        cleared_count=Count("id", filter=Q(status=ChequeReceivable.Status.CLEARED)),
        bounced_count=Count("id", filter=Q(status=ChequeReceivable.Status.BOUNCED)),
    )
    totals = list(
        qs.values("currency")
        .annotate(total=Coalesce(Sum("amount"), Value(ZERO), output_field=MONEY))
        .order_by("currency")
    )
    return {**counts, "totals": totals}


# --- 7. colleague statement is apps.accounting.selectors.counterparty_statement ---
