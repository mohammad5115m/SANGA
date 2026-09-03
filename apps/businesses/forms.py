from __future__ import annotations

from django import forms

from apps.core.forms import HttpsURLField

from .models import Business


class BusinessProfileForm(forms.ModelForm):
    class Meta:
        model = Business
        fields = ("name", "city", "province", "phone", "address", "website")
        field_classes = {"website": HttpsURLField}
        widgets = {
            "name": forms.TextInput(attrs={"class": "field-input"}),
            "city": forms.TextInput(attrs={"class": "field-input"}),
            "province": forms.TextInput(attrs={"class": "field-input"}),
            "phone": forms.TextInput(attrs={"class": "field-input", "dir": "ltr"}),
            "address": forms.Textarea(attrs={"class": "field-input", "rows": 3}),
            "website": forms.URLInput(attrs={"class": "field-input", "dir": "ltr"}),
        }
