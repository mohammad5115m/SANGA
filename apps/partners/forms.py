from __future__ import annotations

from django import forms


class PartnerRequestForm(forms.Form):
    message = forms.CharField(
        label="پیام (اختیاری)",
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={"class": "field-input", "placeholder": "مثلاً خرید عمده تراورتن"}),
    )
