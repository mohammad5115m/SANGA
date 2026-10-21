from __future__ import annotations

import re

_ARABIC_YE = "ي"
_PERSIAN_YE = "ی"
_ARABIC_KE = "ك"
_PERSIAN_KE = "ک"
_ZWNJ = "\u200c"
_NBSP = "\u00a0"
_ARABIC_DECIMAL_SEPARATOR = "\u066b"
_ARABIC_THOUSANDS_SEPARATOR = "\u066c"

# Persian (۰-۹) and Arabic-Indic (٠-٩) digits to ASCII
_DIGIT_TRANSLATION = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def normalize_digits(value: str) -> str:
    """Convert locale digits and separators to Decimal-compatible ASCII."""
    return (
        (value or "")
        .translate(_DIGIT_TRANSLATION)
        .replace(_ARABIC_DECIMAL_SEPARATOR, ".")
        .replace(_ARABIC_THOUSANDS_SEPARATOR, "")
        .replace(",", "")
    )


def normalize_persian_text(value: str) -> str:
    """Normalize common Persian/Arabic orthography differences for search."""
    text = normalize_digits(value).strip()
    text = text.replace(_ARABIC_YE, _PERSIAN_YE).replace(_ARABIC_KE, _PERSIAN_KE)
    text = text.replace(_NBSP, " ")
    text = re.sub(rf"[{_ZWNJ}]+", _ZWNJ, text)
    text = re.sub(r"\s+", " ", text)
    return text


def persian_search_variants(value: str) -> tuple[str, ...]:
    """Equivalent query spellings without changing how stored names display."""
    normalized = normalize_persian_text(value)
    variants = {
        normalized,
        normalized.replace(_ZWNJ, " "),
        normalized.replace(" ", _ZWNJ),
    }
    return tuple(item for item in variants if item)


def normalize_phone(phone: str) -> str:
    """Normalize Iranian mobile numbers to 09xxxxxxxxx when possible.

    Accepts Persian/Arabic-Indic digits (common on mobile keyboards).
    """
    digits = re.sub(r"[^0-9]", "", normalize_digits(phone or ""))
    if digits.startswith("98") and len(digits) == 12:
        digits = "0" + digits[2:]
    if digits.startswith("9") and len(digits) == 10:
        digits = "0" + digits
    return digits
