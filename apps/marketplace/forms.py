from __future__ import annotations

from django import forms


class MarketplaceFilterForm(forms.Form):
    q = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "field-input", "placeholder": "نام سنگ، تأمین‌کننده، سورت..."}),
    )
    stone_type = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "field-input", "placeholder": "نوع سنگ"}),
    )
    color = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "field-input", "placeholder": "رنگ"}),
    )
    min_qty = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "field-input", "placeholder": "حداقل متراژ", "inputmode": "decimal"}),
    )
    only_urgent = forms.BooleanField(
        required=False,
        label="فقط فروش فوری",
        widget=forms.CheckboxInput(attrs={"class": "field-checkbox"}),
    )
    only_followed = forms.BooleanField(
        required=False,
        label="فقط تأمین‌کنندگان دنبال‌شده",
        widget=forms.CheckboxInput(attrs={"class": "field-checkbox"}),
    )


class SaveSearchForm(forms.Form):
    name = forms.CharField(
        label="نام جستجو",
        max_length=150,
        widget=forms.TextInput(attrs={"class": "field-input", "placeholder": "مثلاً تراورتن سفید نما"}),
    )
    notify_enabled = forms.BooleanField(
        required=False,
        initial=True,
        label="اطلاع‌رسانی هنگام موجودی جدید",
        widget=forms.CheckboxInput(attrs={"class": "field-checkbox"}),
    )
