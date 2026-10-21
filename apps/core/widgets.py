"""Jalali companion controls, with canonical input compatibility and no-JS entry."""

from __future__ import annotations

from datetime import date, datetime

from django import forms
from django.utils import timezone
from django.utils.dateparse import parse_time

from .calendar import format_jalali, jalali_parts, local_value, parse_jalali, persian_digits
from .persian import normalize_digits

INVALID_PREFIX = "!jalali:"


class JalaliDateWidget(forms.TextInput):
    template_name = "widgets/jalali.html"
    with_time = False

    def __init__(self, attrs=None, format=None):
        attrs = {**(attrs or {}), "class": (attrs or {}).get("class", "field-input")}
        attrs.pop("type", None)
        super().__init__(attrs=attrs)

    @property
    def media(self):
        return forms.Media(css={"all": ("css/calendar.css",)}, js=("js/calendar.js",))

    def value_from_datadict(self, data, files, name):
        companion = name + "_jalali"
        if companion not in data:
            return data.get(name)
        raw = data.get(companion, "").strip()
        clock = normalize_digits(data.get(name + "_time", "")).strip()
        if not raw and not clock:
            return ""
        try:
            result = parse_jalali(raw).isoformat()
            if self.with_time:
                parsed_time = parse_time(clock)
                if parsed_time is None or parsed_time.tzinfo is not None:
                    raise ValueError("ساعت معتبر نیست.")
                result += "T" + parsed_time.isoformat()
            return result
        except (ValueError, OverflowError):
            # A prefixed invalid value cannot accidentally pass Django's ISO parser.
            return INVALID_PREFIX + raw + "|" + clock

    def value_omitted_from_data(self, data, files, name):
        return name not in data and name + "_jalali" not in data

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        widget = context["widget"]
        widget["attrs"].pop("type", None)
        widget["attrs"].setdefault("id", "id_" + name)
        widget["attrs"].setdefault("dir", "ltr")
        widget["attrs"].setdefault("placeholder", "۱۴۰۵/۰۶/۱۲")
        widget["attrs"].setdefault("autocomplete", "off")
        widget["with_time"] = self.with_time
        widget["invalid"] = widget["attrs"].get("aria-invalid")
        widget["clock"] = ""
        widget["canonical"] = ""
        parsed = local_value(value)
        if isinstance(value, str) and value.startswith(INVALID_PREFIX):
            widget["display"], _, widget["clock"] = value[len(INVALID_PREFIX):].partition("|")
        elif isinstance(parsed, date):
            widget["display"] = format_jalali(parsed)
            widget["canonical"] = parsed.isoformat()
            if isinstance(parsed, datetime):
                widget["clock"] = persian_digits(parsed.strftime("%H:%M:%S"))
                widget["canonical"] = parsed.strftime("%Y-%m-%dT%H:%M:%S")
        else:
            widget["display"] = value or ""
        current = jalali_parts(parsed if isinstance(parsed, date) else timezone.localdate())
        widget["year"], widget["month"] = current.year, current.month
        return context


class JalaliDateTimeWidget(JalaliDateWidget):
    with_time = True


class DateRangeForm(forms.Form):
    """Explicit range validation shared by reports, statements, and invoice lists."""

    def __init__(self, data=None, *, start_name="from", end_name="to", **kwargs):
        super().__init__(data=data, **kwargs)
        self.start_name, self.end_name = start_name, end_name
        self.fields[start_name] = forms.DateField(
            label="از تاریخ", required=False, widget=JalaliDateWidget,
        )
        self.fields[end_name] = forms.DateField(
            label="تا تاریخ", required=False, widget=JalaliDateWidget,
        )

    def clean(self):
        result = super().clean()
        start, end = result.get(self.start_name), result.get(self.end_name)
        if start and end and start > end:
            self.add_error(self.end_name, "تاریخ پایان نباید پیش از تاریخ شروع باشد.")
        return result

    def canonical(self, name):
        value = self.cleaned_data.get(name)
        return value.isoformat() if value else ""
