from __future__ import annotations

from django import forms

from apps.inventory.models import InventoryLot

from .models import CustomCatalog


class StorefrontFilterForm(forms.Form):
    q = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "field-input", "placeholder": "جستجوی سنگ، رنگ، نوع..."}),
    )
    stone_type = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "field-input", "placeholder": "نوع سنگ"}),
    )
    color = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "field-input", "placeholder": "رنگ"}),
    )
    only_urgent = forms.BooleanField(
        required=False,
        label="فقط فروش فوری",
        widget=forms.CheckboxInput(attrs={"class": "field-checkbox"}),
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
        widget=forms.TextInput(attrs={"class": "field-input", "dir": "ltr", "inputmode": "tel", "placeholder": "0912..."}),
    )
    message = forms.CharField(
        label="پیام",
        required=False,
        widget=forms.Textarea(attrs={"class": "field-input", "rows": 3, "placeholder": "مثلاً متراژ مورد نیاز و زمان بارگیری"}),
    )


class CustomCatalogForm(forms.ModelForm):
    lots = forms.ModelMultipleChoiceField(
        label="محموله‌ها",
        queryset=InventoryLot.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = CustomCatalog
        fields = ("title", "customer_name", "custom_message", "expires_at", "is_active")
        widgets = {
            "title": forms.TextInput(attrs={"class": "field-input"}),
            "customer_name": forms.TextInput(attrs={"class": "field-input"}),
            "custom_message": forms.Textarea(attrs={"class": "field-input", "rows": 3}),
            "expires_at": forms.DateTimeInput(attrs={"class": "field-input", "type": "datetime-local"}),
            "is_active": forms.CheckboxInput(attrs={"class": "field-checkbox"}),
        }

    def __init__(self, *args, business=None, **kwargs):
        super().__init__(*args, **kwargs)
        if business is not None:
            self.fields["lots"].queryset = InventoryLot.objects.filter(
                business=business,
                archived_at__isnull=True,
            ).select_related("product").order_by("-updated_at")
        if self.instance and self.instance.pk:
            self.fields["lots"].initial = self.instance.items.values_list("lot_id", flat=True)
