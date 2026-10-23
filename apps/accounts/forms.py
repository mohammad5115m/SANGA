from __future__ import annotations

from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from apps.core.persian import normalize_digits, normalize_phone

from .models import User


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("full_name",)
        labels = {"full_name": "نام کامل"}
        help_texts = {"full_name": "این نام در فعالیت‌ها و اسناد حساب شما نمایش داده می‌شود؛ حداکثر ۱۵۰ نویسه."}
        widgets = {"full_name": forms.TextInput(attrs={"class": "field-input", "autocomplete": "name"})}


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


class PasswordLoginForm(PhoneLoginForm):
    password = forms.CharField(
        label="رمز عبور",
        strip=False,
        widget=forms.PasswordInput(attrs={"class": "field-input", "autocomplete": "current-password"}),
    )


class RegistrationForm(PhoneLoginForm):
    full_name = forms.CharField(
        label="نام و نام خانوادگی مالک",
        min_length=2,
        max_length=150,
        widget=forms.TextInput(attrs={"class": "field-input", "autocomplete": "name"}),
    )
    business_name = forms.CharField(
        label="نام کسب‌وکار", min_length=2, max_length=200, widget=forms.TextInput(attrs={"class": "field-input"})
    )
    city = forms.CharField(
        label="شهر",
        min_length=2,
        max_length=100,
        widget=forms.TextInput(attrs={"class": "field-input", "autocomplete": "address-level2"}),
    )
    password1 = forms.CharField(
        label="رمز عبور",
        strip=False,
        max_length=128,
        min_length=8,
        widget=forms.PasswordInput(attrs={"class": "field-input", "autocomplete": "new-password"}),
        help_text="حداقل ۸ نویسه؛ از رمز رایج یا صرفاً عددی استفاده نکنید.",
    )
    password2 = forms.CharField(
        label="تکرار رمز عبور",
        strip=False,
        max_length=128,
        widget=forms.PasswordInput(attrs={"class": "field-input", "autocomplete": "new-password"}),
    )
    field_order = ["full_name", "phone", "business_name", "city", "password1", "password2"]

    def clean(self):
        data = super().clean()
        password = data.get("password1")
        if password and data.get("password2") and password != data["password2"]:
            self.add_error("password2", "دو رمز عبور یکسان نیستند.")
        if password:
            user = User(phone=data.get("phone", ""), full_name=data.get("full_name", ""))
            try:
                validate_password(password, user=user)
            except ValidationError as exc:
                self.add_error("password1", exc)
        return data
