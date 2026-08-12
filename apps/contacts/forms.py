from __future__ import annotations

from django import forms

from apps.businesses.models import Business

from .selectors import linkable_businesses


class ContactForm(forms.Form):
    display_name = forms.CharField(
        label="نام / نام کسب‌وکار",
        max_length=200,
        widget=forms.TextInput(attrs={"class": "field-input", "placeholder": "مثلاً آقای رضایی / سنگ آریا"}),
    )
    phone = forms.CharField(
        label="موبایل/تلفن",
        max_length=32,
        required=False,
        widget=forms.TextInput(attrs={"class": "field-input", "placeholder": "۰۹...", "inputmode": "tel"}),
    )
    address = forms.CharField(
        label="آدرس",
        required=False,
        widget=forms.Textarea(attrs={"class": "field-input", "rows": 2}),
    )
    notes = forms.CharField(
        label="یادداشت",
        required=False,
        widget=forms.Textarea(attrs={"class": "field-input", "rows": 3}),
    )
    linked_business = forms.ModelChoiceField(
        label="اتصال به همکار در سنگا (اختیاری)",
        queryset=Business.objects.none(),
        required=False,
        empty_label="— بدون اتصال —",
        widget=forms.Select(attrs={"class": "field-input"}),
    )

    def __init__(self, *args, business: Business, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["linked_business"].queryset = linkable_businesses(business)
