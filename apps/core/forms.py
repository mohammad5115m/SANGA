"""Form fields and input normalization shared across apps."""

from __future__ import annotations

from django import forms

from apps.core.persian import normalize_digits


class HttpsURLField(forms.URLField):
    """Read a scheme-less URL as HTTPS without deprecated global settings."""

    def __init__(self, *args, **kwargs):
        kwargs["assume_scheme"] = kwargs.get("assume_scheme") or "https"
        super().__init__(*args, **kwargs)


class PersianNumericFormMixin:
    """Normalize Persian/Arabic digits before Django parses numeric fields.

    ``DecimalField`` and ``IntegerField`` correctly reject malformed values, but
    Python's decimal parser only understands ASCII digits. Mobile keyboards in
    the Persian locale commonly submit ۰۱۲۳, so conversion belongs before field
    validation rather than in every individual ``clean_*`` method.
    """

    numeric_fields: tuple[str, ...] = ()

    def __init__(self, *args, **kwargs):
        prefix = kwargs.get("prefix")
        data = args[0] if args else kwargs.get("data")
        if data is not None:
            normalized = data.copy()
            for field_name in self.numeric_fields:
                key = f"{prefix}-{field_name}" if prefix else field_name
                if key in normalized:
                    normalized[key] = normalize_digits(normalized.get(key, ""))
            if args:
                args = (normalized, *args[1:])
            else:
                kwargs["data"] = normalized
        super().__init__(*args, **kwargs)
