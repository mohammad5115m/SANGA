from __future__ import annotations

from django.db.models import Q, QuerySet

from apps.businesses.models import Business

from .models import SalesInvoice


def invoices_for(business: Business) -> QuerySet[SalesInvoice]:
    """Invoices this business issued or received.

    Both directions, because a colleague needs to see what was billed to them
    just as much as the seller needs to see what they billed.
    """
    return (
        SalesInvoice.objects.filter(Q(seller_business=business) | Q(buyer_business=business))
        .select_related("seller_business", "buyer_business", "trade")
        .prefetch_related("items")
    )


def issued_by(business: Business) -> QuerySet[SalesInvoice]:
    return invoices_for(business).filter(seller_business=business)


def invoices_between(business: Business, colleague: Business) -> QuerySet[SalesInvoice]:
    """Everything exchanged with one colleague, in either direction."""
    return invoices_for(business).filter(
        Q(seller_business=business, buyer_business=colleague)
        | Q(seller_business=colleague, buyer_business=business)
    )


def get_invoice(business: Business, invoice_id) -> SalesInvoice | None:
    """Visible to both parties, and to nobody else."""
    return invoices_for(business).filter(pk=invoice_id).first()


def filter_invoices(qs: QuerySet[SalesInvoice], *, status: str = "", q: str = "") -> QuerySet[SalesInvoice]:
    if status:
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(Q(number__icontains=q) | Q(buyer_name__icontains=q))
    return qs
