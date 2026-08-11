from __future__ import annotations

from django import forms


class ReservationRequestForm(forms.Form):
    quantity_sqm = forms.DecimalField(
        label="متراژ درخواستی (m²)",
        min_value=0.001,
        decimal_places=3,
        max_digits=12,
        widget=forms.NumberInput(attrs={"class": "field-input", "step": "0.001", "inputmode": "decimal"}),
    )
    notes = forms.CharField(
        label="توضیحات (اختیاری)",
        required=False,
        widget=forms.Textarea(attrs={"class": "field-input", "rows": 3}),
    )


class ExtendReservationForm(forms.Form):
    hours = forms.IntegerField(
        label="تمدید به مدت (ساعت)",
        min_value=1,
        initial=48,
        widget=forms.NumberInput(attrs={"class": "field-input", "inputmode": "numeric"}),
    )
