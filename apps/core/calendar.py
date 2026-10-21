"""Presentation-only Persian calendar conversions; model dates remain Gregorian."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

import jdatetime
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from apps.core.persian import normalize_digits

MONTHS = (
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
)
WEEKDAYS = ("شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه")
_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def persian_digits(value) -> str:
    return str(value).translate(_DIGITS)


def calendar_value(value):
    """Parse canonical values without interpreting date-only strings as timestamps."""
    if isinstance(value, str):
        try:
            return parse_date(value) or parse_datetime(value)
        except ValueError:
            return None
    return value


def local_value(value):
    value = calendar_value(value)
    if isinstance(value, datetime) and timezone.is_aware(value):
        return timezone.localtime(value)
    return value


def jalali_parts(value) -> jdatetime.date:
    value = local_value(value)
    if isinstance(value, datetime):
        value = value.date()
    if not isinstance(value, date):
        raise ValueError("تاریخ معتبر نیست.")
    return jdatetime.date.fromgregorian(date=value)


def format_jalali(value, *, with_time=False, digits=True) -> str:
    value = local_value(value)
    if not isinstance(value, date):
        return "—"
    parts = jalali_parts(value)
    result = f"{parts.year:04d}/{parts.month:02d}/{parts.day:02d}"
    if with_time and isinstance(value, datetime):
        result += f"، {value:%H:%M}"
    return persian_digits(result) if digits else result


def parse_jalali(value: str) -> date:
    value = normalize_digits(value).strip()
    if not re.fullmatch(r"[0-9]{4}/[0-9]{1,2}/[0-9]{1,2}", value):
        raise ValueError("تاریخ شمسی را به شکل ۱۴۰۵/۰۶/۱۲ وارد کنید.")
    year, month, day = map(int, value.split("/"))
    try:
        return jdatetime.date(year, month, day).togregorian()
    except (ValueError, OverflowError) as exc:
        raise ValueError("روز، ماه یا سال شمسی معتبر نیست.") from exc


def calendar_month(year: int, month: int) -> dict:
    first = jdatetime.date(year, month, 1)
    first_gregorian = first.togregorian()
    count = 31 if month <= 6 else 30 if month < 12 or first.isleap() else 29
    days = []
    for offset in range(count):
        canonical = first_gregorian + timedelta(days=offset)
        days.append({
            "iso": canonical.isoformat(),
            "value": persian_digits(f"{year:04d}/{month:02d}/{offset + 1:02d}"),
            "label": persian_digits(offset + 1),
            "name": f"{persian_digits(offset + 1)} {MONTHS[month - 1]} {persian_digits(year)}",
        })
    return {
        "year": year, "month": month,
        "label": f"{MONTHS[month - 1]} {persian_digits(year)}",
        "offset": (first_gregorian.weekday() + 2) % 7,
        "weekdays": WEEKDAYS, "days": days,
        "today": timezone.localdate().isoformat(),
    }
