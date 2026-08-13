from __future__ import annotations

from django import forms
from django.utils import timezone

from apps.businesses.models import Business

_TEXT = {"class": "field-input"}


class ManualInvoiceForm(forms.Form):
    """Header of a hand-typed invoice.

    The buyer is either a colleague Business or a walk-in customer named inline.
    A walk-in never becomes a platform User.
    """

    buyer_business = forms.ModelChoiceField(
        label="همکار",
        queryset=Business.objects.none(),
        required=False,
        empty_label="— مشتری عادی (بدون حساب سنگا) —",
        widget=forms.Select(attrs=_TEXT),
    )
    customer_name = forms.CharField(
        label="نام مشتری",
        required=False,
        max_length=150,
        widget=forms.TextInput(attrs=_TEXT),
    )
    customer_phone = forms.CharField(
        label="موبایل مشتری",
        required=False,
        max_length=20,
        widget=forms.TextInput(attrs={**_TEXT, "dir": "ltr", "inputmode": "tel"}),
    )
    issue_date = forms.DateField(
        label="تاریخ صدور",
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={**_TEXT, "type": "date"}, format="%Y-%m-%d"),
    )
    notes = forms.CharField(
        label="توضیحات",
        required=False,
        widget=forms.Textarea(attrs={**_TEXT, "rows": 2}),
    )

    def __init__(self, *args, business=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["issue_date"].input_formats = ["%Y-%m-%d"]
        if business is not None:
            from apps.businesses.directory import colleague_businesses

            self.fields["buyer_business"].queryset = colleague_businesses(business)

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("buyer_business") and not (cleaned.get("customer_name") or "").strip():
            raise forms.ValidationError("یک همکار انتخاب کنید یا نام مشتری را وارد کنید.")
        return cleaned


class InvoiceLineForm(forms.Form):
    product_name = forms.CharField(
        label="نام محصول",
        required=False,
        max_length=200,
        widget=forms.TextInput(attrs=_TEXT),
    )
    stone_type = forms.CharField(
        label="نوع سنگ", required=False, max_length=100, widget=forms.TextInput(attrs=_TEXT)
    )
    grade = forms.CharField(
        label="سورت", required=False, max_length=50, widget=forms.TextInput(attrs=_TEXT)
    )
    quantity = forms.DecimalField(
        label="مقدار (m²)",
        required=False,
        min_value=0,
        decimal_places=3,
        max_digits=12,
        widget=forms.NumberInput(attrs={**_TEXT, "step": "0.001"}),
    )
    unit_price = forms.DecimalField(
        label="قیمت واحد (ریال)",
        required=False,
        min_value=0,
        decimal_places=0,
        max_digits=14,
        widget=forms.NumberInput(attrs={**_TEXT, "inputmode": "numeric"}),
    )

    def clean(self):
        cleaned = super().clean()
        # A completely empty row is a spare line the user did not fill in, not an
        # error. A partly-filled one is a mistake worth reporting.
        filled = any(cleaned.get(key) not in (None, "") for key in ("product_name", "quantity", "unit_price"))
        if not filled:
            return cleaned
        if not (cleaned.get("product_name") or "").strip():
            self.add_error("product_name", "نام محصول را وارد کنید.")
        if cleaned.get("quantity") in (None, ""):
            self.add_error("quantity", "مقدار را وارد کنید.")
        if cleaned.get("unit_price") in (None, ""):
            self.add_error("unit_price", "قیمت را وارد کنید.")
        return cleaned


InvoiceLineFormSet = forms.formset_factory(InvoiceLineForm, extra=3, can_delete=True)
