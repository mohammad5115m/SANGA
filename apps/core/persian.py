from __future__ import annotations

import re

_ARABIC_YE = "ي"
_PERSIAN_YE = "ی"
_ARABIC_KE = "ك"
_PERSIAN_KE = "ک"
_ZWNJ = "\u200c"
_NBSP = "\u00a0"


def normalize_persian_text(value: str) -> str:
    """Normalize common Persian/Arabic orthography differences for search."""
    text = value.strip()
    text = text.replace(_ARABIC_YE, _PERSIAN_YE).replace(_ARABIC_KE, _PERSIAN_KE)
    text = text.replace(_NBSP, " ")
    text = re.sub(rf"[{_ZWNJ}]+", _ZWNJ, text)
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_phone(phone: str) -> str:
    """Normalize Iranian mobile numbers to 09xxxxxxxxx when possible."""
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("98") and len(digits) == 12:
        digits = "0" + digits[2:]
    if digits.startswith("9") and len(digits) == 10:
        digits = "0" + digits
    return digits
