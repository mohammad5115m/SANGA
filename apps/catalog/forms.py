from __future__ import annotations

from django import forms

from apps.inventory.models import InventoryLot

from .models import CustomCatalog


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
    lots = forms.ModelMultipleChoiceField(
        label="محصولات (انتخاب دستی)",
        queryset=InventoryLot.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = CustomCatalog
        fields = ("title", "mode", "customer_name", "custom_message", "expires_at", "is_active")
        widgets = {
            "title": forms.TextInput(attrs={"class": "field-input"}),
            "mode": forms.RadioSelect(attrs={"class": "field-checkbox"}),
            "customer_name": forms.TextInput(attrs={"class": "field-input"}),
            "custom_message": forms.Textarea(attrs={"class": "field-input", "rows": 3}),
            "expires_at": forms.DateTimeInput(attrs={"class": "field-input", "type": "datetime-local"}),
            "is_active": forms.CheckboxInput(attrs={"class": "field-checkbox"}),
        }

    def __init__(self, *args, business=None, **kwargs):
        super().__init__(*args, **kwargs)
        if business is not None:
            # Everything the seller owns is selectable, including items that are
            # currently hidden or unavailable: curating is a management action.
            # Whether a selected item actually renders is decided at read time by
            # apps.inventory.policy, not here.
            self.fields["lots"].queryset = (
                InventoryLot.objects.filter(business=business, deleted_at__isnull=True)
                .select_related("product")
                .order_by("-updated_at")
            )
        if self.instance and self.instance.pk:
            # Only the manual includes: exclusions are managed on the detail page,
            # where the seller can see what the rule is currently selecting.
            self.fields["lots"].initial = self.instance.items.filter(
                inclusion="include"
            ).values_list("lot_id", flat=True)
