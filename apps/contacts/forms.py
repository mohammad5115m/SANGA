from __future__ import annotations

from django import forms

from apps.businesses.models import Business

from .selectors import approved_partner_businesses


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
    is_customer = forms.BooleanField(label="مشتری", required=False)
    is_supplier = forms.BooleanField(label="تأمین‌کننده", required=False)
    is_trader = forms.BooleanField(label="واسطه", required=False)
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
        label="اتصال به همکار تأییدشده (اختیاری)",
        queryset=Business.objects.none(),
        required=False,
        empty_label="— بدون اتصال —",
        widget=forms.Select(attrs={"class": "field-input"}),
    )

    def __init__(self, *args, business: Business, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["linked_business"].queryset = approved_partner_businesses(business)

    def clean(self):
        cleaned = super().clean()
        if not (
            cleaned.get("is_customer")
            or cleaned.get("is_supplier")
            or cleaned.get("is_trader")
        ):
            raise forms.ValidationError(
                "حداقل یک نوع ارتباط را انتخاب کنید (مشتری، تأمین‌کننده یا واسطه)."
            )
        return cleaned
