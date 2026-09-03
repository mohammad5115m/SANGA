from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from django import forms
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.utils import timezone

from apps.core.calendar import calendar_month, format_jalali, parse_jalali
from apps.core.views import persian_calendar_month
from apps.core.widgets import DateRangeForm, JalaliDateTimeWidget, JalaliDateWidget


@pytest.mark.parametrize(("jalali", "canonical"), [
    ("1399/12/30", date(2021, 3, 20)),
    ("1400/01/01", date(2021, 3, 21)),
    ("1403/01/01", date(2024, 3, 20)),
    ("1403/12/30", date(2025, 3, 20)),
    ("1404/01/01", date(2025, 3, 21)),
    ("1405/06/12", date(2026, 9, 3)),
])
def test_known_nowruz_and_leap_day_round_trips(jalali, canonical):
    assert parse_jalali(jalali) == canonical
    assert format_jalali(canonical, digits=False) == jalali


@pytest.mark.parametrize("value", ["۱۴۰۵/۰۶/۱۲", "١٤٠٥/٠٦/١٢", "1405/06/12"])
def test_all_three_digit_sets(value):
    assert parse_jalali(value) == date(2026, 9, 3)


@pytest.mark.parametrize("value", ["1400/12/30", "1404/12/30", "1403/13/01", "1403/07/31", "", "2026-09-03"])
def test_invalid_dates_are_rejected(value):
    with pytest.raises(ValueError):
        parse_jalali(value)


def test_timestamp_uses_tehran_and_date_only_never_shifts():
    instant = datetime(2024, 3, 19, 21, 0, tzinfo=ZoneInfo("UTC"))
    with timezone.override("Asia/Tehran"):
        assert format_jalali(instant, with_time=True) == "۱۴۰۳/۰۱/۰۱، ۰۰:۳۰"
    with timezone.override("America/Los_Angeles"):
        assert format_jalali("2024-03-20") == "۱۴۰۳/۰۱/۰۱"
        assert format_jalali(date(2024, 3, 20)) == "۱۴۰۳/۰۱/۰۱"


def test_month_cells_have_canonical_values_and_saturday_first_offset():
    month = calendar_month(1403, 1)
    assert month["days"][0]["iso"] == "2024-03-20"
    assert month["offset"] == 4  # Wednesday in a Saturday-first week.
    assert len(month["days"]) == 31
    assert len(calendar_month(1403, 12)["days"]) == 30
    assert len(calendar_month(1404, 12)["days"]) == 29
    assert month["weekdays"][0] == "شنبه"


def test_calendar_endpoint_is_read_only_and_rejects_invalid_month():
    factory = RequestFactory()
    assert persian_calendar_month(factory.post("/calendar/month/")).status_code == 405
    assert persian_calendar_month(factory.get("/calendar/month/", {"year": 1405, "month": 13})).status_code == 400
    assert persian_calendar_month(factory.get("/calendar/month/", {"year": 1405, "month": 6})).status_code == 200


class DateForm(forms.Form):
    day = forms.DateField(widget=JalaliDateWidget)
    instant = forms.DateTimeField(widget=JalaliDateTimeWidget, required=False)


def test_widget_preserves_existing_iso_clients():
    form = DateForm({"day": "2026-09-03", "instant": "2026-09-03T09:15"})
    assert form.is_valid(), form.errors
    assert form.cleaned_data["day"] == date(2026, 9, 3)


def test_widget_parses_companion_values_without_javascript():
    form = DateForm({
        "day": "2000-01-01", "day_jalali": "۱۴۰۵/۰۶/۱۲",
        "instant_jalali": "۱۴۰۵/۰۶/۱۲", "instant_time": "۰۹:۱۵",
    })
    with timezone.override("Asia/Tehran"):
        assert form.is_valid(), form.errors
    assert form.cleaned_data["day"] == date(2026, 9, 3)
    assert form.cleaned_data["instant"].isoformat() == "2026-09-03T09:15:00+03:30"


def test_invalid_companion_cannot_fall_back_to_hidden_canonical_date():
    form = DateForm({"day": "2026-09-03", "day_jalali": "2026-09-03"})
    assert not form.is_valid()
    assert 'value="2026-09-03"' in str(form["day"])
    assert "2026-09-03|".replace("|", "") in str(form["day"])


def test_historical_tehran_nonexistent_time_is_rejected():
    form = DateForm({"day": "2021-03-22", "instant_jalali": "۱۴۰۰/۰۱/۰۲", "instant_time": "00:30"})
    with timezone.override("Asia/Tehran"):
        assert not form.is_valid()
    assert "instant" in form.errors


def test_reversed_date_range_is_not_silently_accepted():
    form = DateRangeForm({"from_jalali": "۱۴۰۵/۰۶/۱۲", "to_jalali": "۱۴۰۵/۰۶/۱۱"})
    assert not form.is_valid()
    assert "to" in form.errors


def test_widget_render_keeps_label_id_and_form_prefix():
    form = DateForm(initial={"day": date(2026, 9, 3)}, prefix="test")
    html = str(form["day"])
    assert 'id="id_test-day"' in html
    assert 'name="test-day_jalali"' in html
    assert 'name="test-day"' in html
    assert "۱۴۰۵/۰۶/۱۲" in html


def test_datetime_widget_displays_24_hour_persian_time():
    widget = JalaliDateTimeWidget()
    html = widget.render("moment", datetime(2026, 9, 3, 17, 30))
    assert 'value="۱۷:۳۰:۰۰"' in html
    assert 'name="moment_time"' in html
    assert 'type="time"' not in html  # Browser locale must not introduce AM/PM.


def test_printed_date_uses_the_same_formatter():
    html = render_to_string("invoicing/document.html", {"document": {"issue_date": date(2026, 9, 3)}})
    assert "۱۴۰۵/۰۶/۱۲" in html
    assert "2026" not in html
