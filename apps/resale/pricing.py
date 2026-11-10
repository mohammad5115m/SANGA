"""The opt-in warehouse pricing policy. All amounts are Rial per square metre."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from django.utils import timezone

from apps.core.formatting import format_rial
from apps.pricing.models import LotPrice

from .models import ResaleRule, WholesaleProgram

CENT = Decimal("0.01")
WHOLE = Decimal("1")


class ResaleError(ValueError):
    pass


def required_profit(wholesale, rule):
    raw = rule.margin_value
    if rule.margin_kind == ResaleRule.Margin.PERCENT:
        raw = wholesale * rule.margin_value / 100
    return raw.quantize(CENT, rounding=ROUND_CEILING)


def wholesale_cap(retail, rule):
    cap = (
        retail / (1 + rule.margin_value / 100)
        if rule.margin_kind == ResaleRule.Margin.PERCENT else retail - rule.margin_value
    )
    return max(Decimal(0), cap.quantize(WHOLE, rounding=ROUND_FLOOR))


def _verify_margin(wholesale, retail, rule):
    if wholesale + required_profit(wholesale, rule) > retail:
        cap = wholesale_cap(retail, rule)
        if not cap:
            raise ResaleError("قیمت مشتری برای حداقل سود تعیین‌شده کافی نیست؛ قیمت یا حداقل سود را اصلاح کنید.")
        raise ResaleError(f"برای حفظ حداقل سود، قیمت همکاری باید حداکثر {format_rial(cap)} باشد.")


def _fixed(price, at):
    return (
        price is not None and price.tier.is_active and price.mode == LotPrice.Mode.FIXED
        and price.currency == "IRR" and price.amount is not None and price.price_expires_at is not None
        and price.price_expires_at > at
    )


def _live_special(price, at):
    return (
        price is not None and price.tier.is_active and price.mode == LotPrice.Mode.FIXED
        and price.price_expires_at and price.price_expires_at > at
        and price.special_amount is not None and price.special_until and price.special_until > at
    )


def special_end(price, at):
    if _live_special(price, at) and price.currency == "IRR":
        return min(price.special_until, price.price_expires_at)
    return None


def price_rows(lot):
    if hasattr(lot, "resale_prices"):
        rows = lot.resale_prices
    else:
        rows = lot.prices.select_related("tier").filter(tier__code__in=("b2b", "b2c"))
    return {row.tier.code: row for row in rows}


def validate_pair(rows, *, rule=None, at=None):
    at = at or timezone.now()
    b2b, b2c = rows.get("b2b"), rows.get("b2c")
    b2b_end, b2c_end = special_end(b2b, at), special_end(b2c, at)
    if _live_special(b2c, at) and b2c.currency != "IRR":
        raise ResaleError("فروش ویژهٔ انبار باید به ریال قیمت‌گذاری شود.")
    if b2c_end and (not b2b_end or b2b_end != b2c_end or b2b.special_until != b2c.special_until):
        raise ResaleError("انبار نمی‌تواند فقط تخفیف مصرف‌کننده بگذارد؛ هر دو تخفیف باید هم‌زمان پایان یابند.")
    if rule is not None:
        if not (_fixed(b2b, at) and _fixed(b2c, at)):
            raise ResaleError("برای فروش از انبار، قیمت معتبر همکاری و مصرف‌کننده را با هم تعیین کنید.")
        if b2b.price_expires_at != b2c.price_expires_at:
            raise ResaleError("اعتبار قیمت عادی همکاری و مصرف‌کننده باید یکسان باشد.")
        _verify_margin(b2b.amount, b2c.amount, rule)
        if b2c_end:
            _verify_margin(b2b.special_amount, b2c.special_amount, rule)


def validate_saved_prices(lot, *, at):
    if WholesaleProgram.objects.filter(warehouse_id=lot.business_id).exists():
        validate_pair(price_rows(lot), rule=ResaleRule.objects.filter(lot_id=lot.pk).first(), at=at)


@dataclass(frozen=True)
class Terms:
    wholesale: Decimal
    floor: Decimal
    expires_at: timezone.datetime
    wholesale_special_until: timezone.datetime | None


def current_terms(rule, *, at=None):
    at = at or timezone.now()
    rows = price_rows(rule.lot)
    try:
        validate_pair(rows, rule=rule, at=at)
    except ResaleError:
        return None
    b2b, b2c = rows["b2b"], rows["b2c"]
    wholesale_end, retail_end = special_end(b2b, at), special_end(b2c, at)
    return Terms(
        wholesale=b2b.special_amount if wholesale_end else b2b.amount,
        floor=b2c.special_amount if retail_end else b2c.amount,
        expires_at=min(d for d in (b2b.price_expires_at, wholesale_end, retail_end) if d is not None),
        wholesale_special_until=wholesale_end,
    )


@dataclass(frozen=True)
class OfferPrice:
    amount: Decimal | None
    special: bool = False
    expires_at: timezone.datetime | None = None


def offer_price(offer, terms, *, at=None):
    at = at or timezone.now()
    if terms is None:
        return OfferPrice(None)
    if (
        terms.wholesale_special_until and offer.special_amount is not None and offer.special_until
        and offer.special_until > at and offer.special_amount >= terms.floor
    ):
        return OfferPrice(offer.special_amount, True, min(terms.expires_at, offer.special_until))
    if offer.amount >= terms.floor:
        return OfferPrice(offer.amount, expires_at=terms.expires_at)
    return OfferPrice(None)
