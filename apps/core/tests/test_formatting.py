from decimal import Decimal

from apps.core.formatting import format_decimal, format_rial


def test_integer_trailing_zeroes_are_not_trimmed():
    assert format_decimal(100) == "100"
    assert format_decimal(1000, grouped=True) == "1,000"


def test_only_fractional_zeroes_are_trimmed():
    assert format_decimal(Decimal("100.500")) == "100.5"
    assert format_rial(Decimal("2500000.00")) == "2,500,000 ریال"
