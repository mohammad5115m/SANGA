from __future__ import annotations

from django import forms

from .models import Business, Warehouse


class BusinessProfileForm(forms.ModelForm):
    class Meta:
        model = Business
        fields = ("name", "city", "province", "phone", "address", "website")
        widgets = {
            "name": forms.TextInput(attrs={"class": "field-input"}),
            "city": forms.TextInput(attrs={"class": "field-input"}),
            "province": forms.TextInput(attrs={"class": "field-input"}),
            "phone": forms.TextInput(attrs={"class": "field-input", "dir": "ltr"}),
            "address": forms.Textarea(attrs={"class": "field-input", "rows": 3}),
            "website": forms.URLInput(attrs={"class": "field-input", "dir": "ltr"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["website"].assume_scheme = "https"


class WarehouseForm(forms.ModelForm):
    class Meta:
        model = Warehouse
        fields = ("name", "city", "address", "is_default")
        widgets = {
            "name": forms.TextInput(attrs={"class": "field-input", "placeholder": "انبار مرکزی"}),
            "city": forms.TextInput(attrs={"class": "field-input"}),
            "address": forms.Textarea(attrs={"class": "field-input", "rows": 3}),
            "is_default": forms.CheckboxInput(attrs={"class": "field-checkbox"}),
        }
