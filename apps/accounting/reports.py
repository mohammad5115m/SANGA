from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db.models import QuerySet
from django.utils import timezone

from apps.businesses.models import Business
from apps.contacts.models import Contact

from .models import LedgerEntry

ZERO = Decimal("0.00")

# Age buckets for outstanding receivables, in order. The keys are also the field
# names on ``Aging`` so a bucket is never labeled with the wrong amount.
BUCKETS: tuple[tuple[str, str], ...] = (
    ("current", "جاری (۰ تا ۳۰ روز)"),
    ("days_31_60", "۳۱ تا ۶۰ روز"),
    ("days_61_90", "۶۱ تا ۹۰ روز"),
    ("over_90", "بیش از ۹۰ روز"),
)


@dataclass(frozen=True)
class Aging:
    """Outstanding receivable («مطالبات») split into age buckets.

    ``unapplied_credit`` is credit left over after every debit was settled, i.e.
    the magnitude by which the account is «بستانکار». A «بستانکار» or «تسویه»
    account produces no aging amounts at all — you cannot be overdue on money you
    do not owe.
    """

    as_of: date
    current: Decimal = ZERO
    days_31_60: Decimal = ZERO
    days_61_90: Decimal = ZERO
    over_90: Decimal = ZERO
    unapplied_credit: Decimal = ZERO

    @property
    def total(self) -> Decimal:
        """جمع مطالبات معوق: the outstanding receivable across all buckets."""
        return self.current + self.days_31_60 + self.days_61_90 + self.over_90

    @property
    def has_outstanding(self) -> bool:
        return self.total > ZERO

    @property
    def rows(self) -> list[dict]:
        """Labeled buckets for display, in age order."""
        return [
            {"key": key, "label": label, "amount": getattr(self, key)}
            for key, label in BUCKETS
        ]


@dataclass
class _Group:
    contact: Contact
    entries: list[LedgerEntry] = field(default_factory=list)


def _bucket_key(age_days: int) -> str:
    """Bucket an outstanding debit by its age in days.

    A future-dated debit (negative age) counts as جاری rather than falling out of
    the report.
    """
    if age_days <= 30:
        return "current"
    if age_days <= 60:
        return "days_31_60"
    if age_days <= 90:
        return "days_61_90"
    return "over_90"


def live_entries(business: Business, contact: Contact | None = None) -> QuerySet[LedgerEntry]:
    """Entries that still carry financial weight, oldest business date first.

    A reversed entry and its reversal cancel out, so both are dropped instead of
    letting the reversal behave like a payment against some *other* debit. The
    original is recognised by its ``reversed_at`` stamp (backfilled in migration
    ``0003``, and written in the same transaction as every reversal since), and the
    reversal itself by its type.

    Ordered by ``occurred_on`` then ``created_at``: FIFO allocation is about
    business dates, and posting order only breaks ties.
    """
    qs = LedgerEntry.objects.filter(business=business, reversed_at__isnull=True).exclude(
        entry_type=LedgerEntry.Type.REVERSAL
    )
    if contact is not None:
        qs = qs.filter(contact=contact)
    return qs.order_by("occurred_on", "created_at")


def aging_from_entries(entries, as_of: date) -> Aging:
    """FIFO aging over an oldest-first sequence of live entries for one contact.

    Credits (payments received, purchases, credit adjustments) are pooled and then
    applied against the **oldest outstanding debit first**, so a partial payment
    clears the oldest invoice instead of being spread thinly across all of them —
    that is what makes the «بیش از ۹۰ روز» bucket meaningful. What survives of each
    debit is bucketed by the age of *that debit's* ``occurred_on``.

    ``Decimal`` throughout; the caller passes ``entries`` already ordered.
    """
    debits: list[LedgerEntry] = []
    credit_pool = ZERO
    for entry in entries:
        if entry.balance_delta > 0:
            debits.append(entry)
        elif entry.balance_delta < 0:
            credit_pool += -entry.balance_delta

    buckets = {key: ZERO for key, _ in BUCKETS}
    for entry in debits:
        outstanding = entry.balance_delta
        applied = min(credit_pool, outstanding)
        credit_pool -= applied
        outstanding -= applied
        if outstanding <= ZERO:
            continue
        buckets[_bucket_key((as_of - entry.occurred_on).days)] += outstanding

    return Aging(as_of=as_of, unapplied_credit=credit_pool, **buckets)


def contact_aging(business: Business, contact: Contact, *, as_of: date | None = None) -> Aging:
    """گزارش سنی بدهی for one contact of ``business``.

    Always over the whole account as of ``as_of`` (today by default) — statement
    date/type filters are a viewing device and must not change how old a debt is.
    """
    as_of = as_of or timezone.localdate()
    return aging_from_entries(live_entries(business, contact), as_of)


def business_aging(business: Business, *, as_of: date | None = None) -> dict:
    """Business-wide گزارش سنی بدهی: one row per contact with an outstanding debt.

    FIFO allocation cannot be expressed as a plain aggregate, so the live entries
    of the whole business are fetched in **one** query and grouped per contact in
    memory; the per-contact math is then the same ``aging_from_entries`` used by
    ``contact_aging``.

    Every contact of the business is aged, archived ones included: archiving a
    debtor must not erase the debt from the report. This is what keeps the totals
    reconciled with ``selectors.business_financial_summary``, whose rows are the
    active contacts plus the archived ones with a non-zero balance — an archived
    contact who is «تسویه» allocates to nothing and so contributes zero to both
    sides. ``total.total`` equals «جمع مطالبات» and the summed
    ``unapplied_credit`` equals «جمع دیون».

    Returns ``{"as_of", "total": Aging, "rows": [{"contact", "aging"}]}`` with rows
    sorted by outstanding amount, largest first.
    """
    as_of = as_of or timezone.localdate()
    groups: dict = {}
    for entry in live_entries(business).select_related("contact"):
        group = groups.get(entry.contact_id)
        if group is None:
            group = groups[entry.contact_id] = _Group(contact=entry.contact)
        group.entries.append(entry)

    rows: list[dict] = []
    totals = {key: ZERO for key, _ in BUCKETS}
    unapplied_credit = ZERO
    for group in groups.values():
        aging = aging_from_entries(group.entries, as_of)
        unapplied_credit += aging.unapplied_credit
        for key, _label in BUCKETS:
            totals[key] += getattr(aging, key)
        if aging.has_outstanding:
            rows.append({"contact": group.contact, "aging": aging})

    rows.sort(key=lambda row: row["aging"].total, reverse=True)
    return {
        "as_of": as_of,
        "total": Aging(as_of=as_of, unapplied_credit=unapplied_credit, **totals),
        "rows": rows,
    }
