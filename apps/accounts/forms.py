from __future__ import annotations

from django import forms

from apps.core.persian import normalize_digits, normalize_phone


class PhoneLoginForm(forms.Form):
    phone = forms.CharField(
        label="شماره موبایل",
        max_length=20,
        widget=forms.TextInput(
            attrs={
                "class": "field-input",
                "inputmode": "tel",
                "autocomplete": "tel",
                "placeholder": "۰۹۱۲۳۴۵۶۷۸۹",
                "dir": "ltr",
                "autofocus": True,
            }
        ),
    )

    def clean_phone(self) -> str:
        phone = normalize_phone(self.cleaned_data["phone"])
        if not (phone.startswith("09") and len(phone) == 11):
            raise forms.ValidationError("شماره موبایل معتبر نیست.")
        return phone


class OTPVerifyForm(forms.Form):
    phone = forms.CharField(widget=forms.HiddenInput())
    code = forms.CharField(
        label="کد تأیید",
        max_length=8,
        widget=forms.TextInput(
            attrs={
                "class": "field-input",
                "inputmode": "numeric",
                "autocomplete": "one-time-code",
                "placeholder": "------",
                "dir": "ltr",
                "autofocus": True,
            }
        ),
    )

    def clean_phone(self) -> str:
        return normalize_phone(self.cleaned_data["phone"])

    def clean_code(self) -> str:
        code = normalize_digits(self.cleaned_data["code"].strip())
        if not code.isdigit():
            raise forms.ValidationError("کد باید عددی باشد.")
        return code
