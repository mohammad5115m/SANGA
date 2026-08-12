from __future__ import annotations

from django import forms

_TEXT = {"class": "field-input"}


class PurchaseRequestForm(forms.Form):
    """What a buyer fills in on a product page."""

    requested_qty_sqm = forms.DecimalField(
        label="متراژ مورد نیاز (m²)",
        min_value=0,
        decimal_places=3,
        max_digits=12,
        widget=forms.NumberInput(attrs={**_TEXT, "step": "0.001", "inputmode": "decimal"}),
    )
    proposed_unit_price = forms.DecimalField(
        label="قیمت پیشنهادی شما (ریال، اختیاری)",
        required=False,
        min_value=0,
        decimal_places=0,
        max_digits=14,
        widget=forms.NumberInput(attrs={**_TEXT, "inputmode": "numeric"}),
    )
    buyer_note = forms.CharField(
        label="توضیح",
        required=False,
        widget=forms.Textarea(attrs={**_TEXT, "rows": 3, "placeholder": "زمان بارگیری، مقصد، شرایط..."}),
    )


class PurchaseRequestResponseForm(forms.Form):
    """The seller's reply. Accepting here is agreement, not a sale."""

    final_qty_sqm = forms.DecimalField(
        label="متراژ نهایی (m²)",
        required=False,
        min_value=0,
        decimal_places=3,
        max_digits=12,
        widget=forms.NumberInput(attrs={**_TEXT, "step": "0.001", "inputmode": "decimal"}),
    )
    final_unit_price = forms.DecimalField(
        label="قیمت نهایی (ریال)",
        required=False,
        min_value=0,
        decimal_places=0,
        max_digits=14,
        widget=forms.NumberInput(attrs={**_TEXT, "inputmode": "numeric"}),
    )
    seller_note = forms.CharField(
        label="توضیح شما",
        required=False,
        widget=forms.Textarea(attrs={**_TEXT, "rows": 3}),
    )


class FinalizeSaleForm(forms.Form):
    """Deliberate confirmation, because this is the step that touches the books."""

    note = forms.CharField(
        label="توضیح (اختیاری)",
        required=False,
        widget=forms.Textarea(attrs={**_TEXT, "rows": 2}),
    )
    confirm = forms.BooleanField(
        label="تأیید می‌کنم این فروش انجام شده است",
        widget=forms.CheckboxInput(attrs={"class": "field-checkbox"}),
    )
