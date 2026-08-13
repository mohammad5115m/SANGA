from __future__ import annotations

import uuid

from django import forms

from apps.businesses.models import Business
from apps.inventory.models import InventoryLot

_TEXT = {"class": "field-input"}


class PurchaseRequestForm(forms.Form):
    """What a buyer fills in on a product page."""

    requested_qty_sqm = forms.DecimalField(
        label="متراژ مورد نیاز (m²)",
        min_value=0,
        decimal_places=3,
        max_digits=12,
        widget=forms.NumberInput(attrs={**_TEXT, "step": "0.001", "inputmode": "decimal"}),
    )
    proposed_unit_price = forms.DecimalField(
        label="قیمت پیشنهادی شما (ریال، اختیاری)",
        required=False,
        min_value=0,
        decimal_places=0,
        max_digits=14,
        widget=forms.NumberInput(attrs={**_TEXT, "inputmode": "numeric"}),
    )
    buyer_note = forms.CharField(
        label="توضیح",
        required=False,
        widget=forms.Textarea(attrs={**_TEXT, "rows": 3, "placeholder": "زمان بارگیری، مقصد، شرایط..."}),
    )


class PurchaseRequestResponseForm(forms.Form):
    """The seller's reply. Accepting here is agreement, not a sale."""

    final_qty_sqm = forms.DecimalField(
        label="متراژ نهایی (m²)",
        required=False,
        min_value=0,
        decimal_places=3,
        max_digits=12,
        widget=forms.NumberInput(attrs={**_TEXT, "step": "0.001", "inputmode": "decimal"}),
    )
    final_unit_price = forms.DecimalField(
        label="قیمت نهایی (ریال)",
        required=False,
        min_value=0,
        decimal_places=0,
        max_digits=14,
        widget=forms.NumberInput(attrs={**_TEXT, "inputmode": "numeric"}),
    )
    seller_note = forms.CharField(
        label="توضیح شما",
        required=False,
        widget=forms.Textarea(attrs={**_TEXT, "rows": 3}),
    )


class FinalizeSaleForm(forms.Form):
    """Deliberate confirmation, because this is the step that touches the books."""

    note = forms.CharField(
        label="توضیح (اختیاری)",
        required=False,
        widget=forms.Textarea(attrs={**_TEXT, "rows": 2}),
    )
    confirm = forms.BooleanField(
        label="تأیید می‌کنم این فروش انجام شده است",
        widget=forms.CheckboxInput(attrs={"class": "field-checkbox"}),
    )


class DirectSaleForm(forms.Form):
    """«ثبت فروش مستقیم» — a sale agreed over the phone or at the counter.

    This is the authoritative way to record a colleague sale that never went
    through a purchase request: it creates the Trade, posts both books and
    issues the invoice as one commercial event.
    """

    #: Minted once when the blank form is rendered and carried through every
    #: retry of that attempt, so a double-click, a refresh or a proxy retry all
    #: identify themselves as the same sale rather than as new ones.
    submission_id = forms.UUIDField(widget=forms.HiddenInput)

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
    item = forms.ModelChoiceField(
        label="محصول",
        queryset=InventoryLot.objects.none(),
        required=False,
        empty_label="— خارج از فهرست محصولات —",
        widget=forms.Select(attrs=_TEXT),
    )
    product_name = forms.CharField(
        label="نام محصول (اگر خارج از فهرست است)",
        required=False,
        max_length=200,
        widget=forms.TextInput(attrs=_TEXT),
    )
    quantity_sqm = forms.DecimalField(
        label="متراژ (m²)",
        min_value=0,
        decimal_places=3,
        max_digits=12,
        widget=forms.NumberInput(attrs={**_TEXT, "step": "0.001", "inputmode": "decimal"}),
    )
    unit_price = forms.DecimalField(
        label="قیمت واحد (ریال)",
        min_value=0,
        decimal_places=0,
        max_digits=14,
        widget=forms.NumberInput(attrs={**_TEXT, "inputmode": "numeric"}),
    )
    note = forms.CharField(
        label="توضیح (اختیاری)",
        required=False,
        widget=forms.Textarea(attrs={**_TEXT, "rows": 2}),
    )
    confirm = forms.BooleanField(
        label="تأیید می‌کنم این فروش انجام شده است",
        widget=forms.CheckboxInput(attrs={"class": "field-checkbox"}),
    )

    def __init__(self, *args, business=None, **kwargs):
        kwargs.setdefault("initial", {}).setdefault("submission_id", uuid.uuid4)
        super().__init__(*args, **kwargs)
        if business is not None:
            from apps.businesses.directory import colleague_businesses
            from apps.inventory.selectors import lots_for_business

            self.fields["buyer_business"].queryset = colleague_businesses(business)
            self.fields["item"].queryset = lots_for_business(business)

    def clean(self):
        cleaned = super().clean()
        buyer = cleaned.get("buyer_business")
        customer = (cleaned.get("customer_name") or "").strip()
        if not buyer and not customer:
            raise forms.ValidationError("یک همکار انتخاب کنید یا نام مشتری را وارد کنید.")
        if not cleaned.get("item") and not (cleaned.get("product_name") or "").strip():
            self.add_error("product_name", "محصول را انتخاب کنید یا نام آن را بنویسید.")
        return cleaned
