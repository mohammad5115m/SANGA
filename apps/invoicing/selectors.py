from __future__ import annotations

from django.db.models import Q, QuerySet

from apps.businesses.models import Business

from .models import SalesInvoice

#: What a buyer may see of a document somebody else is writing.
#:
#: A draft is the seller still deciding — the number, the lines, whether to issue
#: it at all. Showing it to the buyer meant they could read a bill that had not
#: been sent, and watch it change. A cancelled document stays visible because a
#: buyer who was sent one needs to see that it was voided rather than find it
#: missing.
BUYER_VISIBLE_STATUSES = (SalesInvoice.Status.ISSUED, SalesInvoice.Status.CANCELLED)


def invoices_for(business: Business) -> QuerySet[SalesInvoice]:
    """Invoices this business issued or received.

    Both directions, because a colleague needs to see what was billed to them
    just as much as the seller needs to see what they billed — but asymmetrically:
    the seller sees all of their own documents, and the buyer sees the ones that
    have actually been issued.
    """
    return (
        SalesInvoice.objects.filter(
            Q(seller_business=business)
            | Q(buyer_business=business, status__in=BUYER_VISIBLE_STATUSES)
        )
        .select_related("seller_business", "buyer_business", "trade")
    )


def invoices_between(business: Business, colleague: Business) -> QuerySet[SalesInvoice]:
    """Everything exchanged with one colleague, in either direction."""
    return invoices_for(business).filter(
        Q(seller_business=business, buyer_business=colleague)
        | Q(seller_business=colleague, buyer_business=business)
    )


def get_invoice(business: Business, invoice_id) -> SalesInvoice | None:
    """Visible to both parties, and to nobody else."""
    return invoices_for(business).filter(pk=invoice_id).first()


def recent_customers(business: Business, *, limit: int = 12) -> list[dict]:
    """Small, snapshot-based suggestions without introducing a CRM model."""
    rows = (
        SalesInvoice.objects.filter(
            seller_business=business,
            counterparty_type=SalesInvoice.Counterparty.CUSTOMER,
        )
        .exclude(customer_name="")
        .values("customer_name", "customer_phone", "buyer_address")
        .order_by("-updated_at")[:50]
    )
    result: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        name = row["customer_name"]
        if name in seen:
            continue
        seen.add(name)
        result.append(
            {
                "name": name,
                "phone": row["customer_phone"],
                "address": row["buyer_address"],
            }
        )
        if len(result) >= limit:
            break
    return result


def filter_invoices(
    qs: QuerySet[SalesInvoice],
    *,
    business: Business,
    status: str = "",
    q: str = "",
    direction: str = "",
    origin: str = "",
    date_from=None,
    date_to=None,
) -> QuerySet[SalesInvoice]:
    if status:
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(
            Q(number__icontains=q)
            | Q(buyer_name__icontains=q)
            | Q(seller_business__name__icontains=q)
        )
    if direction == "sent":
        qs = qs.filter(seller_business=business)
    elif direction == "received":
        qs = qs.filter(buyer_business=business).exclude(seller_business=business)
    if origin == "trade":
        qs = qs.filter(trade__isnull=False)
    elif origin == "manual":
        qs = qs.filter(trade__isnull=True)
    if date_from:
        qs = qs.filter(issue_date__gte=date_from)
    if date_to:
        qs = qs.filter(issue_date__lte=date_to)
    return qs
