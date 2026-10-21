from __future__ import annotations

from django.db.models import Count, Q, QuerySet

from apps.businesses.models import Business
from apps.core.persian import normalize_persian_text

from .models import CustomerLead, Inquiry


def inquiries_for(business: Business) -> QuerySet[Inquiry]:
    return (
        Inquiry.objects.filter(business=business)
        .select_related("lead", "lot", "lot__product", "custom_catalog")
        .prefetch_related("items", "items__item", "items__item__product")
    )


def get_inquiry(business: Business, inquiry_id) -> Inquiry | None:
    return inquiries_for(business).filter(pk=inquiry_id).first()


def filter_inquiries(qs: QuerySet[Inquiry], *, status: str = "", q: str = "") -> QuerySet[Inquiry]:
    if status == "open":
        qs = qs.filter(status__in=Inquiry.OPEN_STATUSES)
    elif status:
        qs = qs.filter(status=status)
    if q:
        term = normalize_persian_text(q)
        qs = qs.filter(Q(name__icontains=term) | Q(phone__icontains=term))
    return qs


def leads_for(business: Business) -> QuerySet[CustomerLead]:
    # Ordered because this queryset is paginated. An unordered one lets the
    # database return rows in any order it likes per query, so a customer can
    # appear on page one and page two while another appears on neither.
    return (
        CustomerLead.objects.filter(business=business)
        .annotate(inquiry_count=Count("inquiries"))
        .order_by("-created_at", "pk")
    )


def get_lead(business: Business, lead_id) -> CustomerLead | None:
    return leads_for(business).filter(pk=lead_id).first()


def filter_leads(qs: QuerySet[CustomerLead], *, q: str = "") -> QuerySet[CustomerLead]:
    """Database-level identity search used outside the CRM presenter."""
    if not q:
        return qs
    term = normalize_persian_text(q)
    return qs.filter(Q(name__icontains=term) | Q(phone__icontains=term))
