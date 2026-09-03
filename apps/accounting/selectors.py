from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Case, Count, DecimalField, F, Q, QuerySet, Sum, Value, When
from django.db.models.functions import Coalesce

from apps.businesses.models import Business

from .models import LedgerEntry

ZERO = Decimal("0.00")
CENTS = Decimal("0.01")

# Output field for balance arithmetic done in the database.
BALANCE_FIELD = DecimalField(max_digits=18, decimal_places=2)

# Accounting state of a balance, from the owning business's books:
#   debtor   (بدهکار)   — balance > 0, the colleague owes the business (a receivable)
#   creditor (بستانکار) — balance < 0, the business owes the colleague (a payable)
#   settled  (تسویه)    — balance == 0
BALANCE_STATE_LABELS: dict[str, str] = {
    "debtor": "بدهکار",
    "creditor": "بستانکار",
    "settled": "تسویه",
}

# Sort keys accepted by the ledger index, mapped to safe order_by tuples so the
# query string can never inject an arbitrary column.
BALANCE_SORTS: dict[str, tuple[str, ...]] = {
    "name": ("name",),
    "debtor": ("-balance", "name"),
    "creditor": ("balance", "name"),
}


def historical_counterparties(business: Business) -> QuerySet[Business]:
    """Businesses this one has commercial records with, whatever their state now.

    Deliberately **not** the colleague directory. The directory answers "who may
    I trade with today", which changes when a Business is suspended or leaves the
    network. A statement, an invoice and an outstanding debt answer "who did I
    trade with", which never changes. Resolving the second question through the
    first meant a debtor's statement started returning 404 the moment the
    platform suspended them, and the money owed became unsettleable.
    """
    return Business.objects.exclude(pk=business.pk).filter(
        Q(counterparty_ledger_entries__business=business)
        | Q(received_invoices__seller_business=business)
        | Q(sales_invoices__buyer_business=business)
        | Q(purchases__seller_business=business)
        | Q(trades__buyer_business=business)
    )


def accounting_counterparty(business: Business, counterparty_id) -> Business | None:
    """Resolve a counterparty for ledger reading and posting.

    Two ways in, and both are legitimate: a currently eligible colleague (so a
    first entry can be posted against somebody with no history yet), or a
    Business this one already has records with (so history stays reachable and
    settleable after the network relationship ends).

    An unrelated Business that is neither remains invisible, which is the tenant
    boundary this resolver exists to keep.
    """
    from apps.businesses.directory import get_colleague

    colleague = get_colleague(business, counterparty_id)
    if colleague is not None:
        return colleague
    return historical_counterparties(business).filter(pk=counterparty_id).distinct().first()


def current_balance(business: Business, counterparty: Business) -> Decimal:
    """Current balance = the running balance of the most recent entry.

    No independently-editable balance is stored; the value is always derived
    from the immutable ledger. Posting is serialized per counterparty, so the
    latest ``balance_after`` is authoritative.
    """
    last = (
        LedgerEntry.objects.filter(business=business, counterparty_business=counterparty)
        .order_by("-created_at")
        .first()
    )
    return last.balance_after if last else ZERO


def counterparty_balances(
    business: Business,
    *,
    state: str = "",
    sort: str = "",
) -> QuerySet[Business]:
    """Colleagues this business has a ledger with, annotated as ``balance``.

    One row per counterparty, summed from the immutable ``balance_delta``
    column in a single query rather than by calling :func:`current_balance` per
    colleague. The two agree by construction: every entry's delta is included in
    the running balance exactly once.

    Only colleagues that actually have entries appear. A directory of every
    Business on the platform is a different question, answered by
    ``businesses.directory``; the ledger index is about accounts that exist.

    ``state`` narrows to one accounting state, filtering on the annotation so it
    becomes a HAVING clause rather than a Python pass.
    """
    qs = (
        Business.objects.exclude(pk=business.pk)
        .annotate(
            balance=Coalesce(
                Sum(
                    "counterparty_ledger_entries__balance_delta",
                    filter=Q(counterparty_ledger_entries__business=business),
                ),
                Value(ZERO),
                output_field=BALANCE_FIELD,
            ),
            entry_count=Count(
                "counterparty_ledger_entries",
                filter=Q(counterparty_ledger_entries__business=business),
            ),
        )
        .filter(entry_count__gt=0)
    )
    if state == "debtor":
        qs = qs.filter(balance__gt=0)
    elif state == "creditor":
        qs = qs.filter(balance__lt=0)
    elif state == "settled":
        qs = qs.filter(balance=0)
    return qs.order_by(*BALANCE_SORTS.get(sort, BALANCE_SORTS["name"]))


def business_financial_summary(business: Business) -> dict:
    """Receivable/payable totals across every colleague account.

    Answers «چه کسی به من بدهکار است؟» at the business level in one query: the
    per-colleague balances are aggregated in the database, never by looping over
    ledger entries in Python.

    ``payable_total`` is exposed as a positive magnitude so it is never rendered
    with a minus sign; the label carries the direction instead.
    """
    positive = Case(
        When(balance__gt=0, then=F("balance")),
        default=Value(ZERO),
        output_field=BALANCE_FIELD,
    )
    negative = Case(
        When(balance__lt=0, then=F("balance")),
        default=Value(ZERO),
        output_field=BALANCE_FIELD,
    )
    agg = counterparty_balances(business).aggregate(
        receivable_total=Coalesce(Sum(positive), Value(ZERO), output_field=BALANCE_FIELD),
        payable_total=Coalesce(Sum(negative), Value(ZERO), output_field=BALANCE_FIELD),
        net_balance=Coalesce(Sum("balance"), Value(ZERO), output_field=BALANCE_FIELD),
        debtor_count=Count("pk", filter=Q(balance__gt=0)),
        creditor_count=Count("pk", filter=Q(balance__lt=0)),
        settled_count=Count("pk", filter=Q(balance=0)),
        contact_count=Count("pk"),
    )
    receivable_total = _money(agg["receivable_total"])
    payable_total = _money(-agg["payable_total"])
    net_balance = _money(agg["net_balance"])
    return {
        "receivable_total": receivable_total,
        "payable_total": payable_total,
        "net_balance": net_balance,
        "net": describe_balance(net_balance),
        "debtor_count": agg["debtor_count"],
        "creditor_count": agg["creditor_count"],
        "settled_count": agg["settled_count"],
        "contact_count": agg["contact_count"],
    }


def _money(value) -> Decimal:
    """Coerce a possibly-``None`` database aggregate to a quantized ``Decimal``."""
    if value is None:
        return ZERO
    return Decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP)


def describe_balance(balance: Decimal) -> dict:
    """An unambiguous, labeled description of a balance.

    Standard Persian bookkeeping vocabulary, from the owning business's books: a
    positive balance makes the colleague «بدهکار», a negative one «بستانکار»,
    zero is «تسویه». ``amount`` is always the absolute magnitude, so callers
    render the label instead of a minus sign.

    Never expose a bare signed number without this label — "-500,000" tells the
    reader nothing about who owes whom.
    """
    if balance > 0:
        return {
            "state": "debtor",
            "label": BALANCE_STATE_LABELS["debtor"],
            "amount": balance,
            "signed": balance,
        }
    if balance < 0:
        return {
            "state": "creditor",
            "label": BALANCE_STATE_LABELS["creditor"],
            "amount": -balance,
            "signed": balance,
        }
    return {
        "state": "settled",
        "label": BALANCE_STATE_LABELS["settled"],
        "amount": ZERO,
        "signed": ZERO,
    }


def counterparty_statement(
    business: Business,
    counterparty: Business,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    entry_type: str = "",
) -> QuerySet[LedgerEntry]:
    """Tenant-scoped statement for one colleague, oldest first so the running
    balance reads top to bottom.
    """
    qs = LedgerEntry.objects.filter(business=business, counterparty_business=counterparty)
    if date_from:
        qs = qs.filter(occurred_on__gte=date_from)
    if date_to:
        qs = qs.filter(occurred_on__lte=date_to)
    if entry_type and entry_type in LedgerEntry.Type.values:
        qs = qs.filter(entry_type=entry_type)
    return qs.select_related("created_by", "related_lot", "related_trade", "reverses").order_by("created_at")


def statement_totals(entries: QuerySet[LedgerEntry]) -> dict:
    """Column totals plus an opening and closing balance for a filtered statement.

    ``entries`` must come from :func:`counterparty_statement`, so the footer
    describes exactly the rows on screen.

    The three balance figures answer three different questions, and conflating
    them is what made the old footer misleading. «مانده پایان دوره» was the last
    visible row's stored ``balance_after`` — a *global* running total that
    includes every entry ever posted, including the ones the filter had hidden.
    Filter a statement to «دریافت» only, and the closing figure reflected sales
    that were nowhere on screen, so closing minus the visible columns did not
    reconcile and there was nothing to say why.

    Now:

    - ``opening`` — the balance immediately before the first visible row;
    - ``debit`` / ``credit`` — the movements actually listed;
    - ``closing`` — the last visible row's running balance;
    - ``account_balance`` — the account's current position, whatever is filtered.

    When the filter is a date range, ``opening + debit − credit == closing``.
    When it is an entry type, it deliberately does not, and ``opening`` is what
    makes the discrepancy legible instead of invisible.
    """
    agg = entries.aggregate(
        debit=Coalesce(
            Sum("amount", filter=Q(balance_delta__gt=0)),
            Value(ZERO),
            output_field=BALANCE_FIELD,
        ),
        credit=Coalesce(
            Sum("amount", filter=Q(balance_delta__lt=0)),
            Value(ZERO),
            output_field=BALANCE_FIELD,
        ),
        row_count=Count("pk"),
    )
    first = entries.first()
    last = entries.last()
    closing = last.balance_after if last is not None else None
    # The running balance already includes the first row's own movement, so the
    # opening figure is that balance with the movement taken back out.
    opening = (first.balance_after - first.balance_delta) if first is not None else None

    return {
        "debit": _money(agg["debit"]),
        "credit": _money(agg["credit"]),
        "row_count": agg["row_count"],
        "opening": opening,
        "opening_balance": describe_balance(opening) if opening is not None else None,
        "closing": closing,
        "closing_balance": describe_balance(closing) if closing is not None else None,
    }


def reversed_entry_ids(business: Business, counterparty: Business) -> set:
    """IDs of entries that already have a reversal, so the UI can hide the
    reverse action and the service can prevent double reversal.
    """
    return set(
        LedgerEntry.objects.filter(
            business=business,
            counterparty_business=counterparty,
            reverses__isnull=False,
        ).values_list("reverses_id", flat=True)
    )


def legacy_entries(business: Business) -> QuerySet[LedgerEntry]:
    """Pre-V2 entries whose Contact had no linked Business.

    They keep their balance and stay readable under the name they were filed
    under, but no new entry can be posted against them. Surfaced so a business
    can see that this money exists rather than wondering where it went.
    """
    return (
        LedgerEntry.objects.filter(business=business, counterparty_business__isnull=True)
        .select_related("contact")
        .order_by("legacy_counterparty_name", "created_at")
    )
