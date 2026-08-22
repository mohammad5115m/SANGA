"""Deterministic, server-authoritative invoice arithmetic."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

MONEY = Decimal("0.01")
QUANTITY = Decimal("0.001")
ZERO = Decimal("0.00")
MAX_MONEY = Decimal("99999999999999.99")
MAX_QUANTITY = Decimal("999999999.999")
DISCOUNT_NONE = "none"
DISCOUNT_AMOUNT = "amount"
DISCOUNT_PERCENT = "percent"
DISCOUNT_TYPES = {DISCOUNT_NONE, DISCOUNT_AMOUNT, DISCOUNT_PERCENT}
CURRENCIES = {"IRR", "EUR", "USD"}
DISPLAY_UNITS = {"IRR", "IRT", "EUR", "USD"}


class InvoiceCalculationError(ValueError):
    pass


def validate_display_unit(currency: str, display_unit: str) -> None:
    if currency not in CURRENCIES or display_unit not in DISPLAY_UNITS:
        raise InvoiceCalculationError("ارز یا واحد نمایشی معتبر نیست.")
    valid = display_unit == currency or (currency == "IRR" and display_unit == "IRT")
    if not valid:
        raise InvoiceCalculationError("واحد نمایشی با ارز فاکتور سازگار نیست.")


def to_storage_amount(value, *, currency: str, display_unit: str, label: str) -> Decimal:
    """Normalize UI amounts; تومان is a denomination of IRR, not an FX rate."""
    validate_display_unit(currency, display_unit)
    parsed = decimal_value(value, label=label)
    if currency == "IRR" and display_unit == "IRT":
        parsed *= Decimal("10")
    return parsed.quantize(MONEY, rounding=ROUND_HALF_UP)


def to_display_amount(value, *, currency: str, display_unit: str) -> Decimal:
    validate_display_unit(currency, display_unit)
    parsed = decimal_value(value, label="مبلغ")
    if currency == "IRR" and display_unit == "IRT":
        parsed /= Decimal("10")
    return parsed.quantize(MONEY, rounding=ROUND_HALF_UP)


def decimal_value(value, *, label: str, quantum: Decimal = MONEY) -> Decimal:
    try:
        result = Decimal(str(value if value not in (None, "") else 0))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise InvoiceCalculationError(f"{label} عدد معتبر نیست.") from exc
    if not result.is_finite():
        raise InvoiceCalculationError(f"{label} عدد معتبر نیست.")
    return result.quantize(quantum, rounding=ROUND_HALF_UP)


def money(value, *, label: str, allow_zero: bool = True) -> Decimal:
    result = decimal_value(value, label=label)
    if result < 0 or (not allow_zero and result == 0):
        comparator = "بزرگ‌تر از صفر" if not allow_zero else "منفی"
        raise InvoiceCalculationError(f"{label} باید {comparator} باشد.")
    if result > MAX_MONEY:
        raise InvoiceCalculationError(f"{label} بیش از حد مجاز است.")
    return result


def quantity(value) -> Decimal:
    result = decimal_value(value, label="مقدار", quantum=QUANTITY)
    if result <= 0:
        raise InvoiceCalculationError("مقدار هر ردیف باید بزرگ‌تر از صفر باشد.")
    if result > MAX_QUANTITY:
        raise InvoiceCalculationError("مقدار هر ردیف بیش از حد مجاز است.")
    return result


def discount_amount(*, base: Decimal, kind: str, value, label: str) -> tuple[Decimal, Decimal]:
    kind = kind or DISCOUNT_NONE
    if kind not in DISCOUNT_TYPES:
        raise InvoiceCalculationError(f"نوع {label} معتبر نیست.")
    parsed = money(value, label=label)
    if kind == DISCOUNT_NONE:
        if parsed:
            raise InvoiceCalculationError(f"برای {label} نوع تخفیف را انتخاب کنید.")
        return ZERO, ZERO
    if kind == DISCOUNT_PERCENT:
        if parsed > 100:
            raise InvoiceCalculationError(f"درصد {label} نمی‌تواند بیشتر از ۱۰۰ باشد.")
        return parsed, (base * parsed / Decimal("100")).quantize(MONEY, rounding=ROUND_HALF_UP)
    if parsed > base:
        raise InvoiceCalculationError(f"{label} نمی‌تواند از مبلغ مربوط بیشتر باشد.")
    return parsed, parsed


@dataclass(frozen=True)
class CalculatedLine:
    source: dict
    quantity: Decimal
    unit_price: Decimal
    gross_total: Decimal
    discount_type: str
    discount_value: Decimal
    discount_amount: Decimal
    line_total: Decimal

    def as_dict(self) -> dict:
        return {**self.source, **{key: value for key, value in self.__dict__.items() if key != "source"}}


@dataclass(frozen=True)
class InvoiceTotals:
    gross_subtotal: Decimal
    line_discount_total: Decimal
    net_items_total: Decimal
    invoice_discount_type: str
    invoice_discount_value: Decimal
    invoice_discount_amount: Decimal
    tax_amount: Decimal
    shipping_amount: Decimal
    adjustment_amount: Decimal
    total_amount: Decimal
    paid_amount: Decimal
    previous_balance_snapshot: Decimal
    previous_balance_included: bool
    amount_due: Decimal

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def calculate_line(line: dict) -> CalculatedLine:
    qty = quantity(line.get("quantity"))
    unit_price = money(line.get("unit_price"), label="قیمت واحد", allow_zero=False)
    gross = (qty * unit_price).quantize(MONEY, rounding=ROUND_HALF_UP)
    if gross > MAX_MONEY:
        raise InvoiceCalculationError("مبلغ ناخالص ردیف بیش از حد مجاز است.")
    kind = line.get("discount_type") or DISCOUNT_NONE
    value, discount = discount_amount(
        base=gross, kind=kind, value=line.get("discount_value"), label="تخفیف ردیف"
    )
    return CalculatedLine(
        source=line,
        quantity=qty,
        unit_price=unit_price,
        gross_total=gross,
        discount_type=kind,
        discount_value=value,
        discount_amount=discount,
        line_total=(gross - discount).quantize(MONEY, rounding=ROUND_HALF_UP),
    )


def calculate_invoice(
    lines: list[dict],
    *,
    invoice_discount_type: str = DISCOUNT_NONE,
    invoice_discount_value=0,
    tax_amount=0,
    shipping_amount=0,
    adjustment_amount=0,
    paid_amount=0,
    previous_balance_snapshot=0,
    previous_balance_included: bool = False,
) -> tuple[list[CalculatedLine], InvoiceTotals]:
    calculated = [calculate_line(line) for line in lines]
    if not calculated:
        raise InvoiceCalculationError("حداقل یک ردیف به فاکتور اضافه کنید.")
    gross = sum((line.gross_total for line in calculated), ZERO)
    line_discounts = sum((line.discount_amount for line in calculated), ZERO)
    if gross > MAX_MONEY:
        raise InvoiceCalculationError("جمع ناخالص فاکتور بیش از حد مجاز است.")
    net_items = (gross - line_discounts).quantize(MONEY, rounding=ROUND_HALF_UP)
    discount_value, invoice_discount = discount_amount(
        base=net_items,
        kind=invoice_discount_type,
        value=invoice_discount_value,
        label="تخفیف کلی",
    )
    tax = money(tax_amount, label="مالیات")
    shipping = money(shipping_amount, label="هزینه ارسال")
    adjustment = money(adjustment_amount, label="تعدیل")
    paid = money(paid_amount, label="مبلغ پرداخت‌شده")
    previous = money(previous_balance_snapshot, label="بدهی قبلی")
    total = (net_items - invoice_discount + tax + shipping + adjustment).quantize(
        MONEY, rounding=ROUND_HALF_UP
    )
    if total > MAX_MONEY:
        raise InvoiceCalculationError("جمع نهایی فاکتور بیش از حد مجاز است.")
    if paid > total:
        raise InvoiceCalculationError("مبلغ پرداخت‌شده نمی‌تواند از مبلغ نهایی بیشتر باشد.")
    due = (total - paid + (previous if previous_balance_included else ZERO)).quantize(
        MONEY, rounding=ROUND_HALF_UP
    )
    return calculated, InvoiceTotals(
        gross_subtotal=gross,
        line_discount_total=line_discounts,
        net_items_total=net_items,
        invoice_discount_type=invoice_discount_type or DISCOUNT_NONE,
        invoice_discount_value=discount_value,
        invoice_discount_amount=invoice_discount,
        tax_amount=tax,
        shipping_amount=shipping,
        adjustment_amount=adjustment,
        total_amount=total,
        paid_amount=paid,
        previous_balance_snapshot=previous,
        previous_balance_included=bool(previous_balance_included),
        amount_due=due,
    )


_ONES = ["", "یک", "دو", "سه", "چهار", "پنج", "شش", "هفت", "هشت", "نه"]
_TEENS = ["ده", "یازده", "دوازده", "سیزده", "چهارده", "پانزده", "شانزده", "هفده", "هجده", "نوزده"]
_TENS = ["", "", "بیست", "سی", "چهل", "پنجاه", "شصت", "هفتاد", "هشتاد", "نود"]
_HUNDREDS = ["", "صد", "دویست", "سیصد", "چهارصد", "پانصد", "ششصد", "هفتصد", "هشتصد", "نهصد"]
_SCALES = ["", "هزار", "میلیون", "میلیارد", "تریلیون", "کوادریلیون"]


def _under_thousand(number: int) -> str:
    parts: list[str] = []
    if number >= 100:
        parts.append(_HUNDREDS[number // 100])
        number %= 100
    if 10 <= number < 20:
        parts.append(_TEENS[number - 10])
    else:
        if number >= 20:
            parts.append(_TENS[number // 10])
        if number % 10:
            parts.append(_ONES[number % 10])
    return " و ".join(parts)


def amount_to_words(value: Decimal | int) -> str:
    number = int(Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if number == 0:
        return "صفر"
    if number < 0:
        return "منفی " + amount_to_words(-number)
    if number >= 1000 ** len(_SCALES):
        return f"{number:,}"
    parts: list[str] = []
    scale = 0
    while number:
        chunk = number % 1000
        if chunk:
            text = _under_thousand(chunk)
            if _SCALES[scale]:
                text += " " + _SCALES[scale]
            parts.append(text)
        number //= 1000
        scale += 1
    return " و ".join(reversed(parts))
