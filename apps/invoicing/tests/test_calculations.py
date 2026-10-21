import json
import subprocess
from decimal import Decimal

import pytest

from apps.invoicing.calculations import (
    InvoiceCalculationError,
    amount_to_words,
    calculate_invoice,
    to_display_amount,
    to_storage_amount,
)


def test_all_financial_components_are_calculated_in_one_order():
    lines, totals = calculate_invoice(
        [
            {
                "quantity": "2",
                "unit_price": "1000",
                "discount_type": "percent",
                "discount_value": "10",
            },
            {
                "quantity": "1",
                "unit_price": "500",
                "discount_type": "amount",
                "discount_value": "100",
            },
        ],
        invoice_discount_type="percent",
        invoice_discount_value="10",
        tax_amount="50",
        shipping_amount="100",
        adjustment_amount="10",
        paid_amount="140",
        previous_balance_snapshot="500",
    )

    assert [line.line_total for line in lines] == [Decimal("1800.00"), Decimal("400.00")]
    assert totals.gross_subtotal == Decimal("2500.00")
    assert totals.line_discount_total == Decimal("300.00")
    assert totals.net_items_total == Decimal("2200.00")
    assert totals.invoice_discount_amount == Decimal("220.00")
    assert totals.total_amount == Decimal("2140.00")
    assert totals.amount_due == Decimal("2000.00")


def test_previous_balance_is_explicitly_excluded_or_included():
    kwargs = {
        "lines": [{"quantity": 1, "unit_price": 1000}],
        "paid_amount": 200,
        "previous_balance_snapshot": 500,
    }
    _lines, excluded = calculate_invoice(**kwargs, previous_balance_included=False)
    _lines, included = calculate_invoice(**kwargs, previous_balance_included=True)

    assert excluded.amount_due == Decimal("800.00")
    assert included.amount_due == Decimal("1300.00")


def test_toman_is_a_denomination_not_an_exchange_rate():
    stored = to_storage_amount("125.55", currency="IRR", display_unit="IRT", label="مبلغ")
    assert stored == Decimal("1255.50")
    assert to_display_amount(stored, currency="IRR", display_unit="IRT") == Decimal("125.55")


def test_money_rounding_is_decimal_half_up_after_quantity_multiplication():
    lines, totals = calculate_invoice([{"quantity": "1.005", "unit_price": "1.00"}])

    assert lines[0].gross_total == Decimal("1.01")
    assert totals.total_amount == Decimal("1.01")


@pytest.mark.parametrize("currency", ["EUR", "USD"])
def test_supported_foreign_currencies_are_stored_without_implicit_conversion(currency):
    stored = to_storage_amount("12.34", currency=currency, display_unit=currency, label="مبلغ")

    assert stored == Decimal("12.34")
    assert to_display_amount(stored, currency=currency, display_unit=currency) == stored


@pytest.mark.parametrize(
    ("lines", "kwargs", "message"),
    [
        ([{"quantity": 0, "unit_price": 1}], {}, "مقدار"),
        ([{"quantity": 1, "unit_price": -1}], {}, "قیمت واحد"),
        (
            [{"quantity": 1, "unit_price": 100, "discount_type": "amount", "discount_value": 101}],
            {},
            "تخفیف ردیف",
        ),
        ([{"quantity": 1, "unit_price": 100}], {"paid_amount": 101}, "پرداخت‌شده"),
        ([{"quantity": 1, "unit_price": "NaN"}], {}, "قیمت واحد"),
        ([{"quantity": "999999999.999", "unit_price": "99999999999999.99"}], {}, "بیش از حد"),
    ],
)
def test_invalid_or_inconsistent_amounts_are_rejected(lines, kwargs, message):
    with pytest.raises(InvoiceCalculationError, match=message):
        calculate_invoice(lines, **kwargs)


def test_unknown_currency_or_incompatible_display_unit_is_rejected():
    with pytest.raises(InvoiceCalculationError):
        to_storage_amount(1, currency="BTC", display_unit="BTC", label="مبلغ")
    with pytest.raises(InvoiceCalculationError):
        to_storage_amount(1, currency="USD", display_unit="IRT", label="مبلغ")


def test_amount_to_words_handles_zero_and_large_grouping():
    assert amount_to_words(0) == "صفر"
    assert "هزار" in amount_to_words(Decimal("1234"))


def test_frontend_estimate_contract_matches_backend_calculator():
    payload = {
        "lines": [
            {
                "quantity": "2.125",
                "unit_price": "1200.50",
                "discount_type": "percent",
                "discount_value": "7.5",
            },
            {
                "quantity": "3",
                "unit_price": "400",
                "discount_type": "amount",
                "discount_value": "125",
            },
        ],
        "invoice_discount_type": "amount",
        "invoice_discount_value": "75",
        "tax_amount": "15",
        "shipping_amount": "45",
        "adjustment_amount": "5",
        "paid_amount": "300",
        "previous_balance_snapshot": "500",
        "previous_balance_included": False,
    }
    _lines, backend = calculate_invoice(
        payload["lines"],
        invoice_discount_type=payload["invoice_discount_type"],
        invoice_discount_value=payload["invoice_discount_value"],
        tax_amount=payload["tax_amount"],
        shipping_amount=payload["shipping_amount"],
        adjustment_amount=payload["adjustment_amount"],
        paid_amount=payload["paid_amount"],
        previous_balance_snapshot=payload["previous_balance_snapshot"],
        previous_balance_included=payload["previous_balance_included"],
    )
    script = (
        "const fs=require('fs');"
        "const c=require('./static/js/invoice_calculator.js');"
        "const p=JSON.parse(fs.readFileSync(0,'utf8'));"
        "process.stdout.write(JSON.stringify(c.calculate(p)));"
    )
    completed = subprocess.run(
        ["node", "-e", script],
        input=json.dumps(payload),
        check=True,
        capture_output=True,
        text=True,
    )
    frontend = json.loads(completed.stdout)

    for field in (
        "gross_subtotal",
        "line_discount_total",
        "net_items_total",
        "invoice_discount_amount",
        "total_amount",
        "amount_due",
    ):
        assert Decimal(str(frontend[field])).quantize(Decimal("0.01")) == getattr(backend, field)
