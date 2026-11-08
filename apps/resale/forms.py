from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django import forms
from django.utils import timezone

from apps.catalog.forms import CustomerIdentityForm
from apps.core.persian import normalize_digits

from .models import ResaleRule


class LocalDecimalField(forms.DecimalField):
    def to_python(self, value):
        if isinstance(value, str):
            value = normalize_digits(value).replace("٫", ".")
        return super().to_python(value)


def amount_field(label, *, required=True, places=0, digits=14, minimum=1):
    return LocalDecimalField(
        label=label, required=required, max_digits=digits, decimal_places=places, min_value=Decimal(str(minimum)),
        widget=forms.TextInput(attrs={"class": "field-input", "dir": "ltr", "inputmode": "decimal"}),
    )


class WarehousePriceForm(forms.Form):
    margin_kind = forms.ChoiceField(label="روش حداقل سود", choices=ResaleRule.Margin.choices)
    margin_value = amount_field("حداقل سود (مبلغ ریال یا درصد)", places=2, digits=16, minimum=0.01)
    wholesale = amount_field("قیمت عادی همکاری (ریال در متر مربع)")
    retail = amount_field("قیمت عادی مصرف‌کنندهٔ انبار (ریال در متر مربع)")
    days = forms.IntegerField(label="اعتبار هر دو قیمت (روز)", min_value=1, max_value=365, initial=7)
    discount = forms.ChoiceField(label="نوع فروش ویژه", choices=[
        ("none", "بدون فروش ویژه"),
        ("wholesale", "فقط تخفیف همکاری"),
        ("paired", "تخفیف همکاری و مصرف‌کننده هم‌زمان"),
    ])
    special_wholesale = amount_field("قیمت ویژهٔ همکاری (ریال)", required=False)
    special_retail = amount_field("قیمت ویژهٔ مصرف‌کننده (ریال؛ فقط حالت هم‌زمان)", required=False)
    special_days = forms.IntegerField(
        label="مدت فروش ویژه از زمان ذخیره (روز)", required=False, min_value=1, max_value=365,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "field-input")

    def clean(self):
        data = super().clean()
        kind, days = data.get("discount"), data.get("special_days")
        if kind == "none":
            if any(data.get(field) is not None for field in ("special_wholesale", "special_retail", "special_days")):
                self.add_error("discount", "برای حذف فروش ویژه، مبلغ‌ها و مدت آن را خالی کنید.")
        elif kind in ("wholesale", "paired"):
            if data.get("special_wholesale") is None:
                self.add_error("special_wholesale", "قیمت ویژهٔ همکاری الزامی است.")
            if kind == "paired" and data.get("special_retail") is None:
                self.add_error("special_retail", "قیمت ویژهٔ مصرف‌کننده الزامی است.")
            if kind == "wholesale" and data.get("special_retail") is not None:
                self.add_error("special_retail", "در این حالت، قیمت ویژهٔ مصرف‌کننده را خالی بگذارید.")
            if days is None or (data.get("days") is not None and days > data["days"]):
                self.add_error("special_days", "مدت ویژه باید حداکثر برابر اعتبار هر دو قیمت باشد.")
        return data

    def price_specs(self):
        data = self.cleaned_data
        until = timezone.now() + timedelta(days=data["special_days"]) if data["discount"] != "none" else None
        result = {}
        for tier, field in (("b2b", "wholesale"), ("b2c", "retail")):
            special = data.get("special_" + field)
            result[tier] = {
                "amount": data[field], "valid_for_days": data["days"],
                "special_amount": special, "special_until": until if special else None,
            }
        return result


class OfferForm(forms.Form):
    amount = amount_field("قیمت فروش شما (ریال در متر مربع)")
    special_amount = amount_field("قیمت ویژهٔ شما (اختیاری، ریال در متر مربع)", required=False)


class PurchaseForm(forms.Form):
    wholesale = forms.DecimalField(widget=forms.HiddenInput, max_digits=16, decimal_places=2)
    retail = forms.DecimalField(widget=forms.HiddenInput, max_digits=16, decimal_places=2)


class ResaleInquiryForm(CustomerIdentityForm):
    quantity = amount_field("متراژ درخواستی", places=3, digits=12, minimum=0.001)
