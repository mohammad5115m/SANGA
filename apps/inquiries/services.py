from __future__ import annotations

import logging

from django.db import transaction

from apps.businesses.models import Business
from apps.core.persian import normalize_phone
from apps.inventory.models import InventoryLot

from .models import Inquiry

logger = logging.getLogger(__name__)


class InquiryError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@transaction.atomic
def create_inquiry(
    *,
    business: Business,
    name: str,
    phone: str,
    message: str = "",
    lot: InventoryLot | None = None,
    custom_catalog=None,
    source: str = Inquiry.Source.STOREFRONT,
    requester=None,
) -> Inquiry:
    name = (name or "").strip()
    phone = normalize_phone(phone or "")
    message = (message or "").strip()

    if len(name) < 2:
        raise InquiryError("نام را وارد کنید.")
    if not (phone.startswith("09") and len(phone) == 11):
        raise InquiryError("شماره موبایل معتبر نیست.")

    if lot is not None and lot.business_id != business.id:
        raise InquiryError("محموله متعلق به این کسب‌وکار نیست.")
    if custom_catalog is not None and custom_catalog.business_id != business.id:
        raise InquiryError("کاتالوگ متعلق به این کسب‌وکار نیست.")

    inquiry = Inquiry.objects.create(
        business=business,
        lot=lot,
        custom_catalog=custom_catalog,
        requester=requester if getattr(requester, "is_authenticated", False) else None,
        name=name,
        phone=phone,
        message=message,
        source=source,
        status=Inquiry.Status.NEW,
    )
    logger.info("Inquiry created business=%s inquiry=%s source=%s", business.id, inquiry.id, source)
    return inquiry
