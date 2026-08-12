from __future__ import annotations

from django import forms

from apps.inventory.models import InventoryLot

from .models import PurchaseRequest


class PurchaseRequestForm(forms.ModelForm):
    class Meta:
        model = PurchaseRequest
        fields = (
            "title",
            "stone_type",
            "category",
            "color",
            "application",
            "required_qty_sqm",
            "thickness_mm",
            "length_cm",
            "width_cm",
            "acceptable_grade",
            "budget_amount",
            "destination_city",
            "required_by",
            "similar_accepted",
            "notes",
            "is_public_to_network",
        )
        widgets = {
            "title": forms.TextInput(attrs={"class": "field-input", "placeholder": "مثلاً تراورتن نمای پروژه"}),
            "stone_type": forms.TextInput(attrs={"class": "field-input", "placeholder": "تراورتن"}),
            "category": forms.TextInput(attrs={"class": "field-input"}),
            "color": forms.TextInput(attrs={"class": "field-input", "placeholder": "کرم / سفید"}),
            "application": forms.TextInput(attrs={"class": "field-input", "placeholder": "نما / کف"}),
            "required_qty_sqm": forms.NumberInput(attrs={"class": "field-input", "step": "0.001"}),
            "thickness_mm": forms.NumberInput(attrs={"class": "field-input", "step": "0.01"}),
            "length_cm": forms.NumberInput(attrs={"class": "field-input", "step": "0.01"}),
            "width_cm": forms.NumberInput(attrs={"class": "field-input", "step": "0.01"}),
            "acceptable_grade": forms.TextInput(attrs={"class": "field-input"}),
            "budget_amount": forms.NumberInput(attrs={"class": "field-input", "step": "1"}),
            "destination_city": forms.TextInput(attrs={"class": "field-input"}),
            "required_by": forms.DateInput(attrs={"class": "field-input", "type": "date"}),
            "similar_accepted": forms.CheckboxInput(attrs={"class": "field-checkbox"}),
            "notes": forms.Textarea(attrs={"class": "field-input", "rows": 3}),
            "is_public_to_network": forms.CheckboxInput(attrs={"class": "field-checkbox"}),
        }


class PurchaseOfferForm(forms.Form):
    unit_price = forms.DecimalField(
        label="قیمت واحد پیشنهادی (همکار)",
        min_value=0,
        decimal_places=0,
        max_digits=14,
        widget=forms.NumberInput(attrs={"class": "field-input", "inputmode": "numeric"}),
    )
    offered_qty_sqm = forms.DecimalField(
        label="متراژ پیشنهادی",
        min_value=0.001,
        decimal_places=3,
        max_digits=12,
        widget=forms.NumberInput(attrs={"class": "field-input", "step": "0.001"}),
    )
    lot = forms.ModelChoiceField(
        label="محموله مرتبط (اختیاری)",
        queryset=InventoryLot.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "field-input"}),
    )
    message = forms.CharField(
        label="پیام خصوصی",
        required=False,
        widget=forms.Textarea(attrs={"class": "field-input", "rows": 3}),
    )

    def __init__(self, *args, business, **kwargs):
        """``business`` is required: the lot choices are the offering business's
        own un-archived lots and nothing else, so a crafted lot id from another
        tenant fails validation before the service is ever called.
        """
        super().__init__(*args, **kwargs)
        self.fields["lot"].queryset = (
            InventoryLot.objects.filter(
                business=business,
                deleted_at__isnull=True,
            )
            .select_related("product")
            .order_by("-updated_at")
        )
