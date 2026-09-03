"""Creating customer inquiries.

The ordering rule that matters: **the inquiry is saved on the server first**, and
only then are share buttons offered. WhatsApp and Telegram are a convenience, not
the delivery mechanism — a seller must never depend on a message the customer may
not have sent.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.businesses.models import Business, BusinessMembership
from apps.businesses.permissions import LEADS_MANAGE
from apps.core.persian import normalize_phone
from apps.inventory.models import InventoryLot
from apps.notifications.services import notify_business

from .models import CustomerLead, Inquiry, InquiryItem

logger = logging.getLogger(__name__)


class InquiryError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def validate_phone(phone: str) -> str:
    phone = normalize_phone(phone or "")
    if not (phone.startswith("09") and len(phone) == 11):
        raise InquiryError("شماره موبایل معتبر نیست. مثال: ۰۹۱۲۳۴۵۶۷۸۹")
    return phone


def _quantity(value):
    if value in (None, ""):
        return None
    try:
        quantity = Decimal(str(value)).quantize(Decimal("0.001"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise InquiryError("متراژ واردشده معتبر نیست.") from exc
    if quantity <= 0:
        raise InquiryError("متراژ باید بزرگ‌تر از صفر باشد.")
    return quantity


@transaction.atomic
def get_or_create_lead(
    *,
    business: Business,
    name: str,
    phone: str,
    verified: bool = False,
) -> CustomerLead:
    """Find this customer by phone, or record them.

    Creating a lead never creates a platform User. A retail customer has no
    account and no way to log in; this row exists only so the seller can see
    that the same person has asked before.
    """
    phone = validate_phone(phone)
    name = (name or "").strip()
    if len(name) < 2:
        raise InquiryError("نام را وارد کنید.")

    lead, created = CustomerLead.objects.get_or_create(
        business=business,
        phone=phone,
        defaults={"name": name},
    )
    updates = []
    if not created and name and lead.name != name:
        # Trust the newest thing they told us: people correct their own name.
        lead.name = name
        updates.append("name")
    if verified and lead.phone_verified_at is None:
        lead.phone_verified_at = timezone.now()
        updates.append("phone_verified_at")
    if updates:
        lead.save(update_fields=[*updates, "updated_at"])
    return lead


@transaction.atomic
def create_inquiry(
    *,
    business: Business,
    name: str,
    phone: str,
    message: str = "",
    items: list[dict] | None = None,
    lot: InventoryLot | None = None,
    custom_catalog=None,
    source: str = Inquiry.Source.PUBLIC_SEARCH,
    requester=None,
    verified: bool = False,
    submission_id=None,
) -> Inquiry:
    """Save one inquiry covering one or more products.

    ``items`` is a list of ``{"item": InventoryLot, "quantity": Decimal|None}``.
    A single-product shortcut is also accepted through ``lot`` so the existing
    detail-page form keeps working.

    Products belonging to another business are rejected outright rather than
    silently dropped: a crafted request must not look like it worked.

    Passing ``submission_id`` makes the call idempotent for this seller: an
    inquiry already recorded under that token is returned rather than twinned.
    """
    if submission_id is not None:
        existing = Inquiry.objects.filter(submission_id=submission_id, business=business).first()
        if existing is not None:
            return existing

    lead = get_or_create_lead(business=business, name=name, phone=phone, verified=verified)

    if custom_catalog is not None and custom_catalog.business_id != business.id:
        raise InquiryError("کاتالوگ متعلق به این کسب‌وکار نیست.")

    rows = list(items or [])
    if lot is not None and not rows:
        rows = [{"item": lot, "quantity": None}]

    for row in rows:
        product = row.get("item")
        if product is None:
            raise InquiryError("محصول انتخاب‌شده معتبر نیست.")
        if product.business_id != business.id:
            raise InquiryError("محصول انتخاب‌شده متعلق به این کسب‌وکار نیست.")

    try:
        with transaction.atomic():
            inquiry = Inquiry.objects.create(
                business=business,
                submission_id=submission_id,
                lead=lead,
                # Kept in sync for the single-product case so the dashboard and
                # older queries still resolve a product without joining
                # InquiryItem.
                lot=rows[0]["item"] if len(rows) == 1 else None,
                custom_catalog=custom_catalog,
                requester=requester if getattr(requester, "is_authenticated", False) else None,
                name=lead.name,
                phone=lead.phone,
                message=(message or "").strip(),
                source=source,
                status=Inquiry.Status.NEW,
            )
    except IntegrityError:
        # Two submissions of the same token reached the insert together. The
        # loser reads the winner's row rather than failing the customer.
        winner = Inquiry.objects.filter(submission_id=submission_id, business=business).first()
        if winner is None:
            raise
        return winner

    seen = set()
    for row in rows:
        product = row["item"]
        if product.pk in seen:
            continue
        seen.add(product.pk)
        InquiryItem.objects.create(
            inquiry=inquiry,
            item=product,
            product_name=product.product.commercial_name,
            requested_qty_sqm=_quantity(row.get("quantity")),
            note=(row.get("note") or "").strip()[:255],
        )

    # After commit, so a notification is never sent for an inquiry that rolled
    # back, and a broken notification backend cannot lose a saved inquiry.
    transaction.on_commit(lambda: _notify_seller(inquiry))
    logger.info(
        "Inquiry created business=%s inquiry=%s items=%s source=%s",
        business.id,
        inquiry.id,
        len(seen),
        source,
    )
    return inquiry


@transaction.atomic
def submit_public_inquiry(
    *,
    submission_id,
    groups: list[dict],
    name: str,
    phone: str,
    message: str = "",
    source: str = Inquiry.Source.PUBLIC_SEARCH,
    requester=None,
    verified: bool = False,
) -> list[Inquiry]:
    """Record one public submission that may span several sellers.

    ``groups`` is ``[{"business": Business, "rows": [...]}, ...]`` — the customer's
    selection already split by seller, because one seller must never see what a
    customer asked another.

    Two properties this function exists for, neither of which the per-seller
    service can provide on its own:

    **All or nothing.** The loop used to run outside any transaction, so a
    failure on the third seller left the first two committed while the page said
    the submission had failed. The customer then had no way to tell which sellers
    had heard them.

    **Retry-safe.** Every inquiry carries the submission token, unique per seller,
    so a refresh, a double-click or a resubmitted form is handed the inquiries
    that already exist instead of creating a second set.
    """
    if not groups:
        raise InquiryError("هنوز محصولی انتخاب نکرده‌اید.")

    return [
        create_inquiry(
            business=group["business"],
            name=name,
            phone=phone,
            message=message,
            items=group["rows"],
            custom_catalog=group.get("custom_catalog"),
            source=source,
            requester=requester,
            verified=verified,
            submission_id=submission_id,
        )
        for group in groups
    ]


@transaction.atomic
def create_stock_inquiry(
    *,
    item: InventoryLot,
    name: str,
    phone: str,
    message: str = "",
    requester=None,
) -> Inquiry:
    """«استعلام موجودی» — a buyer asking whether a stale quantity still holds.

    Recorded as a normal inquiry so it lands in the same inbox, with wording that
    tells the seller what is actually being asked. The seller's reply is either
    confirming the stock or marking the product ناموجود; both are one click from
    the notification.
    """
    inquiry = create_inquiry(
        business=item.business,
        name=name,
        phone=phone,
        message=(message or "").strip() or "درخواست استعلام موجودی",
        items=[{"item": item, "quantity": None}],
        source=Inquiry.Source.ITEM_DETAIL,
        requester=requester,
    )
    return inquiry


def _notify_seller(inquiry: Inquiry) -> None:
    """Tell the members who handle customer leads.

    Was OWNER and MANAGER by role, which excluded exactly the wrong people: the
    default ``staff`` salesperson holds ``leads.manage`` and is the person who
    calls the customer back, and they were the one member guaranteed not to hear
    that a customer had asked about a product.
    """
    product_count = inquiry.items.count()
    body = f"{inquiry.name} ({inquiry.phone})"
    if product_count:
        body += f" · {product_count} محصول"

    notify_business(
        inquiry.business,
        capability=LEADS_MANAGE,
        title="درخواست خرید جدید مشتری",
        body=body,
        link=f"/app/leads/inquiries/{inquiry.id}/",
    )


@transaction.atomic
def set_inquiry_status(*, inquiry: Inquiry, status: str, membership: BusinessMembership) -> Inquiry:
    from apps.businesses.permissions import LEADS_MANAGE

    if membership is None or not membership.has_capability(LEADS_MANAGE):
        raise InquiryError("اجازه پاسخ به استعلام را ندارید.")
    if inquiry.business_id != membership.business_id:
        raise InquiryError("این استعلام متعلق به کسب‌وکار شما نیست.")
    if status not in Inquiry.Status.values:
        raise InquiryError("وضعیت نامعتبر است.")

    inquiry.status = status
    if status == Inquiry.Status.CONTACTED and inquiry.contacted_at is None:
        inquiry.contacted_at = timezone.now()
    inquiry.save(update_fields=["status", "contacted_at", "updated_at"])
    return inquiry


@transaction.atomic
def mark_inquiry_viewed(*, inquiry: Inquiry) -> Inquiry:
    if inquiry.viewed_at is None:
        inquiry.viewed_at = timezone.now()
        inquiry.save(update_fields=["viewed_at", "updated_at"])
    return inquiry
