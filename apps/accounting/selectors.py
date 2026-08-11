from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Count, DecimalField, Q, QuerySet, Sum, Value
from django.db.models.functions import Coalesce

from apps.businesses.models import Business
from apps.contacts.models import Contact
from apps.pricing.models import LotPrice

from .models import TRADE_ENTRY_TYPES, LedgerEntry

ZERO = Decimal("0.00")
CENTS = Decimal("0.01")


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


def contact_balances(business: Business) -> QuerySet[Contact]:
    """Active contacts of ``business`` with their balance annotated as ``balance``.

    The ledger index needs one row per contact, so the balance is summed from the
    immutable ``balance_delta`` column in the same query instead of calling
    ``current_balance`` per contact. The two agree by construction: every entry's
    delta is included in the running balance exactly once.
    """
    return (
        Contact.objects.filter(business=business, is_active=True)
        .annotate(
            balance=Coalesce(
                Sum(
                    "ledger_entries__balance_delta",
                    filter=Q(ledger_entries__business=business),
                ),
                Value(ZERO),
                output_field=DecimalField(max_digits=18, decimal_places=2),
            ),
            entry_count=Count(
                "ledger_entries",
                filter=Q(ledger_entries__business=business),
            ),
        )
        .order_by("display_name")
    )


def describe_balance(balance: Decimal) -> dict:
    """Return an unambiguous, labeled description of a balance.

    Never expose a bare signed number without stating who owes whom.
    """
    if balance > 0:
        return {"state": "they_owe", "label": "طلب ما از این مخاطب", "amount": balance}
    if balance < 0:
        return {"state": "we_owe", "label": "بدهی ما به این مخاطب", "amount": -balance}
    return {"state": "settled", "label": "تسویه", "amount": ZERO}


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


def trade_entry_for_reservation(business: Business, reservation) -> LedgerEntry | None:
    """The *live* trade entry recorded for ``reservation`` in this business's
    ledger, or ``None``.

    A reversed trade does not count as recorded, exactly like the
    ``uniq_trade_entry_per_reservation`` constraint: reversing frees the slot so
    the seller can re-record the trade with the correct amount and keep the
    reservation link.
    """
    return (
        LedgerEntry.objects.filter(
            business=business,
            related_reservation=reservation,
            entry_type__in=TRADE_ENTRY_TYPES,
            reversed_at__isnull=True,
        )
        .select_related("contact")
        .first()
    )


def suggested_contact_for_reservation(business: Business, reservation) -> Contact | None:
    """Preselect a contact only when the mapping is unambiguous: exactly one
    active contact of ``business`` is linked to the reservation's requester
    business. Anything else (none, or several) is the seller's decision.
    """
    matches = list(
        Contact.objects.filter(
            business=business,
            linked_business_id=reservation.requester_business_id,
            is_active=True,
        )[:2]
    )
    return matches[0] if len(matches) == 1 else None


def suggested_trade_amount(reservation) -> Decimal | None:
    """Suggested trade amount = lot B2B unit price × reserved quantity.

    Returns ``None`` when no per-square-metre price applies (missing price, or an
    inquiry-only / per-slab price), so the seller has to type the amount instead
    of being handed a wrong number.
    """
    price = (
        LotPrice.objects.filter(
            lot_id=reservation.lot_id,
            tier__code="b2b",
            tier__is_active=True,
            unit=LotPrice.Unit.PER_SQM,
        )
        .order_by("-updated_at")
        .first()
    )
    if price is None or price.amount <= 0:
        return None
    quantity = Decimal(str(reservation.quantity_sqm))
    return (Decimal(str(price.amount)) * quantity).quantize(CENTS, rounding=ROUND_HALF_UP)


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
