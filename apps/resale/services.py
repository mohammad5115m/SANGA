from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from apps.businesses.eligibility import business_can_sell
from apps.businesses.models import Business, BusinessMembership
from apps.businesses.permissions import PRICES_EDIT
from apps.inventory.models import InventoryLot
from apps.pricing.models import LotPrice
from apps.pricing.services import _quantize_amount, lock_price_lot, set_lot_prices

from .models import ResaleGrant, ResaleOffer, ResaleRule, WholesaleProgram
from .pricing import ResaleError, current_terms, price_rows, validate_pair
from .selectors import available_rules


def require_actor(membership, business_id, capability):
    if membership is None:
        raise ResaleError("اجازهٔ انجام این عملیات را ندارید.")
    actor = BusinessMembership.objects.select_related("business", "user").filter(
        pk=membership.pk, business_id=business_id, status=BusinessMembership.Status.ACTIVE, user__is_active=True,
    ).first()
    if actor is None or not actor.has_capability(capability):
        raise ResaleError("اجازهٔ انجام این عملیات را ندارید.")
    if not business_can_sell(actor.business):
        raise ResaleError("این عملیات برای کسب‌وکار فعال، تأییدشده و دارای پلن فروش در دسترس است.")
    return actor


@transaction.atomic
def enable_program(*, warehouse, membership):
    warehouse = Business.objects.select_for_update(no_key=True).get(pk=warehouse.pk)
    require_actor(membership, warehouse.pk, PRICES_EDIT)
    program = WholesaleProgram.objects.filter(warehouse=warehouse).first()
    if program:
        return program
    at = timezone.now()
    # Starting the program must not leave legacy B2C-only promotions visible.
    live_retail = LotPrice.objects.filter(
        lot__business=warehouse, lot__deleted_at__isnull=True, tier__code="b2c", tier__is_active=True,
        mode=LotPrice.Mode.FIXED, special_amount__isnull=False, special_until__gt=at, price_expires_at__gt=at,
    ).select_related("lot")
    for retail in live_retail:
        try:
            validate_pair(price_rows(retail.lot), at=at)
        except ResaleError as exc:
            raise ResaleError(f"ابتدا فروش ویژهٔ محصول {retail.lot.lot_code} را اصلاح کنید: {exc}") from exc
    return WholesaleProgram.objects.create(warehouse=warehouse)


@transaction.atomic
def set_grant(*, warehouse, membership, retailer, active=True):
    Business.objects.select_for_update(no_key=True).get(pk=warehouse.pk)
    require_actor(membership, warehouse.pk, PRICES_EDIT)
    program = WholesaleProgram.objects.filter(warehouse_id=warehouse.pk).first()
    if program is None:
        raise ResaleError("ابتدا فروش از انبار را فعال کنید.")
    if retailer.pk == warehouse.pk or (active and not business_can_sell(Business.objects.get(pk=retailer.pk))):
        raise ResaleError("همکار انتخاب‌شده واجد شرایط فروش نیست.")
    grant, _ = ResaleGrant.objects.update_or_create(
        program=program, retailer=retailer, defaults={"is_active": active},
    )
    return grant


@transaction.atomic
def configure_lot(*, lot, membership, margin_kind, margin_value, specs):
    lot = lock_price_lot(lot)
    require_actor(membership, lot.business_id, PRICES_EDIT)
    if lot.deleted_at or not WholesaleProgram.objects.filter(warehouse_id=lot.business_id).exists():
        raise ResaleError("این محصول برای قیمت‌گذاری انبار در دسترس نیست.")
    margin_value = _quantize_amount(margin_value)
    if margin_kind not in ResaleRule.Margin.values or margin_value <= 0:
        raise ResaleError("نوع و مقدار حداقل سود معتبر نیست.")
    if set(specs) != {"b2b", "b2c"}:
        raise ResaleError("هر دو قیمت همکاری و مشتری لازم است.")
    rule, _ = ResaleRule.objects.update_or_create(
        lot=lot, defaults={"margin_kind": margin_kind, "margin_value": margin_value},
    )
    set_lot_prices(lot=lot, specs=specs)
    return rule


@transaction.atomic
def set_offer(*, rule, retailer, membership, amount, special_amount=None, special_until=None):
    lot = lock_price_lot(rule.lot)
    require_actor(membership, retailer.pk, PRICES_EDIT)
    grant = ResaleGrant.objects.filter(
        program__warehouse_id=lot.business_id, retailer=retailer, is_active=True,
    ).exclude(program__warehouse=retailer).first()
    current = available_rules().select_related("lot").filter(pk=rule.pk, lot_id=lot.pk).first()
    if grant is None or current is None:
        raise ResaleError("برای عرضهٔ این محصول دسترسی ندارید.")
    now = timezone.now()
    terms = current_terms(current, at=now)
    if terms is None:
        raise ResaleError("ابتدا انبار باید هر دو قیمت را تأیید کند.")
    amount = _quantize_amount(amount)
    if amount < terms.floor:
        raise ResaleError("قیمت شما نمی‌تواند از قیمت فعلی مصرف‌کنندهٔ انبار کمتر باشد.")
    if special_amount is not None:
        special_amount = _quantize_amount(special_amount)
        if special_amount < terms.floor or special_amount >= amount:
            raise ResaleError("قیمت ویژه باید از قیمت عادی خودتان کمتر و دست‌کم برابر کف انبار باشد.")
        if not (special_until and terms.wholesale_special_until and now < special_until <= terms.expires_at):
            raise ResaleError("فروش ویژه فقط در بازهٔ معتبر تخفیف همکاری مجاز است.")
    elif special_until is not None:
        raise ResaleError("برای زمان پایان فروش ویژه، مبلغ آن را هم وارد کنید.")
    return ResaleOffer.objects.update_or_create(grant=grant, rule=current, defaults={
        "amount": amount, "special_amount": special_amount, "special_until": special_until, "is_active": True,
    })[0]


@transaction.atomic
def unpublish_offer(*, offer, retailer, membership):
    lock_price_lot(offer.rule.lot)
    require_actor(membership, retailer.pk, PRICES_EDIT)
    if offer.grant.retailer_id != retailer.pk:
        raise ResaleError("این پیشنهاد متعلق به کسب‌وکار شما نیست.")
    ResaleOffer.objects.filter(pk=offer.pk).update(is_active=False)


def parse_quantity(value):
    try:
        value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ResaleError("متراژ درخواست معتبر نیست.") from exc
    if not value.is_finite() or value <= 0 or value > Decimal("999999999.999") or value.as_tuple().exponent < -3:
        raise ResaleError("متراژ درخواست معتبر نیست.")
    return value


def require_available_quantity(lot: InventoryLot, quantity):
    if quantity < lot.min_sale_qty:
        raise ResaleError("متراژ کمتر از حداقل فروش انبار است.")
    # Unconfirmed stock is allowed for inquiry, never promised as available.
    if lot.available_sqm is not None and quantity > lot.available_sqm:
        raise ResaleError("متراژ از آخرین موجودی ثبت‌شده بیشتر است.")
