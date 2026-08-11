from __future__ import annotations

import logging

from django.db import IntegrityError, transaction

from apps.businesses.models import Business, BusinessMembership
from apps.businesses.permissions import CUSTOMERS_MANAGE

from .models import Contact
from .selectors import is_approved_partner

logger = logging.getLogger(__name__)

DUPLICATE_LINK_RACE = "این همکار هم‌زمان به مخاطب دیگری متصل شد؛ دوباره تلاش کنید."


class ContactError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def _require_manage(membership: BusinessMembership | None) -> None:
    if membership is None or not membership.has_capability(CUSTOMERS_MANAGE):
        raise ContactError("اجازه مدیریت مخاطبین را ندارید.")


def _require_same_business(membership: BusinessMembership, business: Business) -> None:
    if membership.business_id != business.id:
        raise ContactError("دسترسی نامعتبر است.")


def _clean_types(is_customer: bool, is_supplier: bool, is_trader: bool) -> None:
    if not (is_customer or is_supplier or is_trader):
        raise ContactError("حداقل یک نوع ارتباط را انتخاب کنید (مشتری، تأمین‌کننده یا واسطه).")


def _validate_link(
    business: Business,
    linked_business: Business | None,
    *,
    exclude_contact: Contact | None = None,
) -> None:
    if linked_business is None:
        return
    if linked_business.id == business.id:
        raise ContactError("نمی‌توانید کسب‌وکار خودتان را به‌عنوان مخاطب متصل کنید.")
    if not is_approved_partner(business, linked_business):
        raise ContactError("اتصال فقط به همکاران تأییدشده امکان‌پذیر است.")

    # One partner, one ledger: a second contact linked to the same partner would
    # split that partner's balance across two statements. Mirrors the
    # ``uniq_linked_business_per_business`` constraint.
    taken = Contact.objects.filter(business=business, linked_business=linked_business)
    if exclude_contact is not None:
        taken = taken.exclude(pk=exclude_contact.pk)
    existing = taken.first()
    if existing is not None:
        raise ContactError(
            f"«{linked_business.name}» قبلاً به مخاطب «{existing.display_name}» متصل شده است؛ "
            "برای اینکه حساب این همکار تکه‌تکه نشود، فقط یک مخاطب می‌تواند به او متصل باشد."
        )


@transaction.atomic
def create_contact(
    *,
    business: Business,
    membership: BusinessMembership,
    display_name: str,
    phone: str = "",
    address: str = "",
    notes: str = "",
    is_customer: bool = False,
    is_supplier: bool = False,
    is_trader: bool = False,
    linked_business: Business | None = None,
) -> Contact:
    _require_manage(membership)
    _require_same_business(membership, business)

    display_name = (display_name or "").strip()
    if len(display_name) < 2:
        raise ContactError("نام مخاطب خیلی کوتاه است.")

    _clean_types(is_customer, is_supplier, is_trader)
    _validate_link(business, linked_business)

    try:
        contact = Contact.objects.create(
            business=business,
            display_name=display_name,
            phone=(phone or "").strip(),
            address=(address or "").strip(),
            notes=(notes or "").strip(),
            is_customer=is_customer,
            is_supplier=is_supplier,
            is_trader=is_trader,
            linked_business=linked_business,
            created_by=membership.user,
        )
    except IntegrityError as exc:
        # Two concurrent creates for the same partner: the constraint decides.
        raise ContactError(DUPLICATE_LINK_RACE) from exc
    logger.info("Contact created id=%s business=%s", contact.id, business.id)
    return contact


@transaction.atomic
def update_contact(
    *,
    contact: Contact,
    membership: BusinessMembership,
    display_name: str,
    phone: str = "",
    address: str = "",
    notes: str = "",
    is_customer: bool = False,
    is_supplier: bool = False,
    is_trader: bool = False,
    linked_business: Business | None = None,
) -> Contact:
    _require_manage(membership)
    _require_same_business(membership, contact.business)

    display_name = (display_name or "").strip()
    if len(display_name) < 2:
        raise ContactError("نام مخاطب خیلی کوتاه است.")

    _clean_types(is_customer, is_supplier, is_trader)
    _validate_link(contact.business, linked_business, exclude_contact=contact)

    contact.display_name = display_name
    contact.phone = (phone or "").strip()
    contact.address = (address or "").strip()
    contact.notes = (notes or "").strip()
    contact.is_customer = is_customer
    contact.is_supplier = is_supplier
    contact.is_trader = is_trader
    contact.linked_business = linked_business
    try:
        contact.save(
            update_fields=[
                "display_name",
                "phone",
                "address",
                "notes",
                "is_customer",
                "is_supplier",
                "is_trader",
                "linked_business",
                "updated_at",
            ]
        )
    except IntegrityError as exc:
        raise ContactError(DUPLICATE_LINK_RACE) from exc
    logger.info("Contact updated id=%s business=%s", contact.id, contact.business_id)
    return contact


@transaction.atomic
def archive_contact(*, contact: Contact, membership: BusinessMembership) -> Contact:
    _require_manage(membership)
    _require_same_business(membership, contact.business)
    if not contact.is_active:
        return contact
    contact.is_active = False
    contact.save(update_fields=["is_active", "updated_at"])
    logger.info("Contact archived id=%s business=%s", contact.id, contact.business_id)
    return contact


@transaction.atomic
def restore_contact(*, contact: Contact, membership: BusinessMembership) -> Contact:
    _require_manage(membership)
    _require_same_business(membership, contact.business)
    if contact.is_active:
        return contact
    contact.is_active = True
    contact.save(update_fields=["is_active", "updated_at"])
    return contact
