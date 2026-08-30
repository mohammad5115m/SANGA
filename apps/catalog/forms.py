from __future__ import annotations

from django import forms
from django.utils import timezone

from apps.inventory.selectors import lots_for_business

from .models import CustomCatalog, StorefrontCollection


class CustomerIdentityForm(forms.Form):
    """Asked once, at submission. Never before browsing."""

    name = forms.CharField(
        label="نام شما",
        max_length=150,
        widget=forms.TextInput(attrs={"class": "field-input", "autocomplete": "name"}),
    )
    phone = forms.CharField(
        label="شماره موبایل",
        max_length=20,
        widget=forms.TextInput(
            attrs={"class": "field-input", "dir": "ltr", "inputmode": "tel", "placeholder": "0912..."}
        ),
        help_text="کد تأیید به این شماره پیامک می‌شود.",
    )
    message = forms.CharField(
        label="توضیح (اختیاری)",
        required=False,
        widget=forms.Textarea(
            attrs={"class": "field-input", "rows": 3, "placeholder": "زمان نیاز، محل پروژه، شرایط..."}
        ),
    )


class OTPCodeForm(forms.Form):
    code = forms.CharField(
        label="کد تأیید",
        max_length=8,
        widget=forms.TextInput(
            attrs={"class": "field-input", "dir": "ltr", "inputmode": "numeric", "autocomplete": "one-time-code"}
        ),
    )


class InquiryForm(forms.Form):
    name = forms.CharField(
        label="نام",
        max_length=150,
        widget=forms.TextInput(attrs={"class": "field-input", "autocomplete": "name"}),
    )
    phone = forms.CharField(
        label="موبایل",
        max_length=20,
        widget=forms.TextInput(
            attrs={"class": "field-input", "dir": "ltr", "inputmode": "tel", "placeholder": "0912..."}
        ),
    )
    message = forms.CharField(
        label="پیام",
        required=False,
        widget=forms.Textarea(
            attrs={"class": "field-input", "rows": 3, "placeholder": "مثلاً متراژ مورد نیاز و زمان بارگیری"}
        ),
    )


class CustomCatalogForm(forms.ModelForm):
    class Meta:
        model = CustomCatalog
        fields = ("title", "customer_name", "custom_message", "expires_at", "is_active")
        widgets = {
            "title": forms.TextInput(attrs={"class": "field-input"}),
            "customer_name": forms.TextInput(attrs={"class": "field-input"}),
            "custom_message": forms.Textarea(attrs={"class": "field-input", "rows": 3}),
            "expires_at": forms.DateTimeInput(
                attrs={"class": "field-input", "type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "is_active": forms.CheckboxInput(attrs={"class": "field-checkbox"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["expires_at"].label = "تاریخ انقضا"
        self.fields["is_active"].label = "منتشر شود"
        self.fields["expires_at"].help_text = "اختیاری؛ پس از این زمان لینک برای مشتری باز نمی‌شود."
        self.fields["expires_at"].input_formats = [
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ]

    def clean_expires_at(self):
        value = self.cleaned_data.get("expires_at")
        if value is not None and value <= timezone.now():
            raise forms.ValidationError("تاریخ انقضا باید در آینده باشد.")
        return value


class StorefrontCollectionForm(forms.ModelForm):
    products = forms.ModelMultipleChoiceField(
        label="محصولات مجموعه",
        queryset=None,
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "collection-product-check"}),
        help_text="پس از ذخیره می‌توانید ترتیب محصولات را با دکمه‌های بالا و پایین تنظیم کنید.",
    )

    class Meta:
        model = StorefrontCollection
        fields = ("title", "description", "is_active", "suggestion_kind")
        widgets = {
            "title": forms.TextInput(attrs={"class": "field-input"}),
            "description": forms.Textarea(attrs={"class": "field-input", "rows": 2}),
            "is_active": forms.CheckboxInput(attrs={"class": "field-checkbox"}),
            "suggestion_kind": forms.Select(attrs={"class": "field-input"}),
        }

    def __init__(self, *args, business, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["products"].queryset = lots_for_business(business).order_by(
            "product__commercial_name"
        )
        if self.instance and self.instance.pk:
            self.fields["products"].initial = self.instance.items.order_by(
                "sort_order", "id"
            ).values_list("lot_id", flat=True)
