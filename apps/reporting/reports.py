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
from apps.invoicing.models import SalesInvoice
from apps.pricing.models import LotPrice
from apps.trading.models import Trade

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


def _money(value) -> Decimal:
    return Decimal(value or 0).quantize(Decimal("0.01"))


# --- 1. sales by colleague ------------------------------------------------------


def sales_by_colleague(business: Business, window: DateRange) -> list[dict]:
    rows = (
        _trades(business, window)
        .values("buyer_business__id", "buyer_business__name", "counterparty_type", "customer_name")
        .annotate(
            total=Coalesce(Sum("total_amount"), Value(ZERO), output_field=MONEY),
            quantity=Coalesce(Sum("quantity_sqm"), Value(Decimal("0")), output_field=QUANTITY),
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
            "quantity": row["quantity"],
            "trade_count": row["trade_count"],
        }
        for row in rows
    ]


# --- 2. sales by stone type -----------------------------------------------------


def sales_by_stone_type(business: Business, window: DateRange) -> list[dict]:
    """Grouped on the trade's own snapshot, not the live product.

    A product renamed or reclassified after the sale must not silently move
    historical revenue into a different category.
    """
    rows = (
        _trades(business, window)
        .values("stone_type")
        .annotate(
            total=Coalesce(Sum("total_amount"), Value(ZERO), output_field=MONEY),
            quantity=Coalesce(Sum("quantity_sqm"), Value(Decimal("0")), output_field=QUANTITY),
            trade_count=Count("id"),
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
        _trades(business, window)
        .values("product_name")
        .annotate(
            total=Coalesce(Sum("total_amount"), Value(ZERO), output_field=MONEY),
            quantity=Coalesce(Sum("quantity_sqm"), Value(Decimal("0")), output_field=QUANTITY),
            trade_count=Count("id"),
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
    """Headline numbers: total sold, total square metres, trade count."""
    agg = _trades(business, window).aggregate(
        total=Coalesce(Sum("total_amount"), Value(ZERO), output_field=MONEY),
        quantity=Coalesce(Sum("quantity_sqm"), Value(Decimal("0")), output_field=QUANTITY),
        trade_count=Count("id"),
    )
    return {
        "total": _money(agg["total"]),
        "quantity_sqm": agg["quantity"] or Decimal("0"),
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
    qs = SalesInvoice.objects.filter(seller_business=business).select_related("buyer_business")
    return window.apply(qs, "issue_date").order_by("-issue_date")


def invoice_summary(business: Business, window: DateRange) -> dict:
    """Invoice counts and money, with each number meaning one thing.

    ``total`` sums **issued** invoices only. It used to sum everything that was
    not cancelled, which quietly included drafts — documents nobody has been sent,
    that may still change and may never be issued at all. A figure labelled «مبلغ
    فاکتورها» that moves while somebody is typing a draft is not a total of
    anything the business can act on.

    Drafts and cancelled documents are still counted, and drafts get their own
    subtotal, so a voided or unfinished document is visible rather than missing —
    without any single number doing two jobs.
    """
    agg = invoices_in_range(business, window).aggregate(
        issued_total=Coalesce(
            Sum("total_amount", filter=Q(status=SalesInvoice.Status.ISSUED)),
            Value(ZERO),
            output_field=MONEY,
        ),
        draft_total=Coalesce(
            Sum("total_amount", filter=Q(status=SalesInvoice.Status.DRAFT)),
            Value(ZERO),
            output_field=MONEY,
        ),
        issued_count=Count("id", filter=Q(status=SalesInvoice.Status.ISSUED)),
        draft_count=Count("id", filter=Q(status=SalesInvoice.Status.DRAFT)),
        cancelled_count=Count("id", filter=Q(status=SalesInvoice.Status.CANCELLED)),
        total_count=Count("id"),
    )
    return {
        "total": _money(agg["issued_total"]),
        "draft_total": _money(agg["draft_total"]),
        "issued_count": agg["issued_count"],
        "draft_count": agg["draft_count"],
        "cancelled_count": agg["cancelled_count"],
        "total_count": agg["total_count"],
    }


# --- 7. colleague statement is apps.accounting.selectors.counterparty_statement ---
