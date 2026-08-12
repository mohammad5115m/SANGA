from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Case, Count, DecimalField, F, Q, QuerySet, Sum, Value, When
from django.db.models.functions import Coalesce

from apps.businesses.models import Business
from apps.contacts.models import Contact
from apps.purchase_requests.models import PurchaseOffer

from .models import TRADE_ENTRY_TYPES, LedgerEntry

ZERO = Decimal("0.00")
CENTS = Decimal("0.01")

# Output field for balance arithmetic done in the database.
BALANCE_FIELD = DecimalField(max_digits=18, decimal_places=2)

# Accounting state of a balance, from the owning business's books:
#   debtor   (بدهکار)   — balance > 0, the contact owes the business (a receivable)
#   creditor (بستانکار) — balance < 0, the business owes the contact (a payable)
#   settled  (تسویه)    — balance == 0
BALANCE_STATE_LABELS: dict[str, str] = {
    "debtor": "بدهکار",
    "creditor": "بستانکار",
    "settled": "تسویه",
}

# Sort keys accepted by the ledger index, mapped to safe order_by tuples so the
# query string can never inject an arbitrary column.
BALANCE_SORTS: dict[str, tuple[str, ...]] = {
    "name": ("display_name",),
    "debtor": ("-balance", "display_name"),
    "creditor": ("balance", "display_name"),
}


def current_balance(business: Business, contact: Contact) -> Decimal:
    """Current balance = the running balance of the most recent entry.

    No independently-editable balance is stored; the value is always derived
    from the immutable ledger. Posting is serialized per contact, so the latest
    ``balance_after`` is authoritative.
    """
    last = (
        LedgerEntry.objects.filter(business=business, contact=contact)
        .order_by("-created_at")
        .first()
    )
    return last.balance_after if last else ZERO


def contact_balances(
    business: Business,
    *,
    state: str = "",
    sort: str = "",
) -> QuerySet[Contact]:
    """Contacts of ``business`` with their balance annotated as ``balance``.

    The ledger index needs one row per contact, so the balance is summed from the
    immutable ``balance_delta`` column in the same query instead of calling
    ``current_balance`` per contact. The two agree by construction: every entry's
    delta is included in the running balance exactly once.

    Rows are the active contacts **plus every archived contact whose balance is
    not zero**. Archiving is a housekeeping action, not a settlement: hiding an
    archived debtor here would make the money disappear from «جمع مطالبات» and
    from the aging report while the debt still stands. An archived contact whose
    account is «تسویه» carries no money and stays out; the template marks the
    archived rows that remain («بایگانی‌شده») so a stale row is never mistaken
    for a live one.

    ``state`` narrows the rows to one accounting state (``debtor`` / ``creditor`` /
    ``settled``) — filtering on the annotation, so it becomes a ``HAVING`` clause
    rather than a Python pass. ``settled`` includes contacts with no entries at
    all, which is what a zero balance means. ``sort`` picks one of
    ``BALANCE_SORTS``; anything unknown falls back to the name order.
    """
    qs = (
        Contact.objects.filter(business=business)
        .annotate(
            balance=Coalesce(
                Sum(
                    "ledger_entries__balance_delta",
                    filter=Q(ledger_entries__business=business),
                ),
                Value(ZERO),
                output_field=BALANCE_FIELD,
            ),
            entry_count=Count(
                "ledger_entries",
                filter=Q(ledger_entries__business=business),
            ),
        )
        .filter(Q(is_active=True) | ~Q(balance=0))
    )
    if state == "debtor":
        qs = qs.filter(balance__gt=0)
    elif state == "creditor":
        qs = qs.filter(balance__lt=0)
    elif state == "settled":
        qs = qs.filter(balance=0)
    return qs.order_by(*BALANCE_SORTS.get(sort, BALANCE_SORTS["name"]))


def business_financial_summary(business: Business) -> dict:
    """Business-wide receivable/payable totals over every reported contact balance.

    Answers «چه کسی به من بدهکار است؟» at the business level in one query: the
    per-contact balances are aggregated in the database (a sub-query over the
    ``contact_balances`` annotation), never by looping over ledger entries in
    Python.

    Returns a dict with ``Decimal`` money and ``int`` counts:
      ``receivable_total`` — جمع مطالبات: sum of the positive balances.
      ``payable_total``    — جمع دیون: sum of the negative balances, as a positive
                             magnitude (so it is never rendered with a minus sign).
      ``net_balance``      — مانده کل: signed net (receivables − payables).
      ``net``              — ``describe_balance(net_balance)`` for labeled display.
      ``debtor_count`` / ``creditor_count`` / ``settled_count`` / ``contact_count``.

    The rows are exactly ``contact_balances``', so an archived contact who still
    owes money is counted here too and the summary can never understate the books.

    A business with no contacts or no entries yields zeros, not ``None``.
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
    agg = contact_balances(business).aggregate(
        receivable_total=Coalesce(Sum(positive), Value(ZERO), output_field=BALANCE_FIELD),
        payable_total=Coalesce(Sum(negative), Value(ZERO), output_field=BALANCE_FIELD),
        net_balance=Coalesce(Sum("balance"), Value(ZERO), output_field=BALANCE_FIELD),
        debtor_count=Count("pk", filter=Q(balance__gt=0)),
        creditor_count=Count("pk", filter=Q(balance__lt=0)),
        settled_count=Count("pk", filter=Q(balance=0)),
        contact_count=Count("pk"),
    )
    receivable_total = _money(agg["receivable_total"])
    # Stored negative in the database; exposed as a magnitude.
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
    """Return an unambiguous, labeled description of a balance.

    Standard Persian bookkeeping vocabulary, from the owning business's books:
    a positive balance makes the contact «بدهکار», a negative one «بستانکار»,
    and zero is «تسویه». ``amount`` is always the absolute magnitude, so callers
    render the label instead of a minus sign; ``signed`` is kept for callers that
    need the raw value (comparisons, aggregation) rather than display.

    Never expose a bare signed number without this label.
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


def contact_statement(
    business: Business,
    contact: Contact,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    entry_type: str = "",
) -> QuerySet[LedgerEntry]:
    """Tenant-scoped statement for a contact, oldest first so the running
    balance reads top-to-bottom. Optional filters by date range and type.
    """
    qs = LedgerEntry.objects.filter(business=business, contact=contact)
    if date_from:
        qs = qs.filter(occurred_on__gte=date_from)
    if date_to:
        qs = qs.filter(occurred_on__lte=date_to)
    if entry_type and entry_type in LedgerEntry.Type.values:
        qs = qs.filter(entry_type=entry_type)
    return qs.select_related("created_by", "related_lot", "reverses").order_by("created_at")


def statement_totals(entries: QuerySet[LedgerEntry]) -> dict:
    """Column totals and closing balance for an already-filtered statement.

    ``entries`` must be a queryset from ``contact_statement`` (already tenant-scoped
    and filtered), so the footer always describes exactly the rows on screen:
    جمع بدهکار sums the amounts whose ``balance_delta`` is positive, جمع بستانکار the
    negative ones, and each entry counts towards exactly one of the two.

    «مانده پایان دوره» is the running balance of the last row shown, i.e. the same
    number as its مانده cell, so the footer can never contradict the table. It is
    ``None`` when the filters match nothing, because a closing balance for an empty
    period would be an invention.
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
    last = entries.last()
    closing = last.balance_after if last is not None else None
    return {
        "debit": _money(agg["debit"]),
        "credit": _money(agg["credit"]),
        "row_count": agg["row_count"],
        "closing": closing,
        "closing_balance": describe_balance(closing) if closing is not None else None,
    }


def accepted_offer_for(business: Business, offer_id) -> PurchaseOffer | None:
    """An accepted offer this business is a party to, or ``None``.

    Either side may record the trade: the seller who made the offer posts a
    فروش, the buyer who accepted it posts a خرید. A business that is party to
    neither gets ``None`` rather than a hint that the offer exists.
    """
    return (
        PurchaseOffer.objects.filter(
            Q(seller_business=business) | Q(purchase_request__business=business),
            pk=offer_id,
            status=PurchaseOffer.Status.ACCEPTED,
        )
        .select_related("purchase_request", "purchase_request__business", "seller_business", "lot")
        .first()
    )


def trade_entry_for_offer(business: Business, offer) -> LedgerEntry | None:
    """The *live* trade entry recorded for ``offer`` in this business's ledger,
    or ``None``.

    A reversed trade does not count as recorded, exactly like the
    ``uniq_trade_entry_per_offer`` constraint: reversing frees the slot so the
    trade can be re-recorded with the correct amount and keep the offer link.
    """
    return (
        LedgerEntry.objects.filter(
            business=business,
            related_offer=offer,
            entry_type__in=TRADE_ENTRY_TYPES,
            reversed_at__isnull=True,
        )
        .select_related("contact")
        .first()
    )


def offer_counterparty(business: Business, offer) -> Business | None:
    """The other side of ``offer`` from ``business``'s point of view."""
    if offer.seller_business_id == business.id:
        return offer.purchase_request.business
    if offer.purchase_request.business_id == business.id:
        return offer.seller_business
    return None


def suggested_contact_for_offer(business: Business, offer) -> Contact | None:
    """Preselect a contact only when the mapping is unambiguous: exactly one
    active contact of ``business`` is linked to the counterparty business.
    Anything else (none, or several) is the user's decision.
    """
    counterparty = offer_counterparty(business, offer)
    if counterparty is None:
        return None
    matches = list(
        Contact.objects.filter(
            business=business,
            linked_business_id=counterparty.id,
            is_active=True,
        )[:2]
    )
    return matches[0] if len(matches) == 1 else None


def suggested_amount_for_offer(offer) -> Decimal | None:
    """Suggested trade amount = the offer's unit price × its offered quantity.

    This is what both sides already agreed on, so it needs no price lookup.
    Returns ``None`` for a zero-priced offer (a «استعلام»-style quote), so the
    amount has to be typed instead of being handed a wrong number.
    """
    unit_price = Decimal(str(offer.unit_price))
    if unit_price <= 0:
        return None
    quantity = Decimal(str(offer.offered_qty_sqm))
    return (unit_price * quantity).quantize(CENTS, rounding=ROUND_HALF_UP)


def reversed_entry_ids(business: Business, contact: Contact) -> set:
    """IDs of entries that already have a reversal, so the UI can hide the
    reverse action and the service can prevent double reversal.
    """
    return set(
        LedgerEntry.objects.filter(
            business=business,
            contact=contact,
            reverses__isnull=False,
        ).values_list("reverses_id", flat=True)
    )
