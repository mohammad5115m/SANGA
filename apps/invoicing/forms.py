from __future__ import annotations

from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone

from apps.core.forms import PersianNumericFormMixin
from apps.inventory.models import InventoryLot

_TEXT = {"class": "field-input"}


class ManualInvoiceForm(forms.Form):
    """Header of a hand-typed invoice for a walk-in customer.

    A walk-in never becomes a platform User. There is deliberately no colleague
    option: a sale to another Business moves that colleague's account, and the
    only thing allowed to move an account is a finalized Trade — so colleague
    sales are recorded through the bilateral «توافق معامله» flow instead.
    """

    customer_name = forms.CharField(
        label="نام مشتری",
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


class InvoiceLineForm(PersianNumericFormMixin, forms.Form):
    numeric_fields = ("quantity", "unit_price")
    item = forms.ModelChoiceField(
        label="محصول ثبت‌شده",
        queryset=InventoryLot.objects.none(),
        required=False,
        widget=forms.HiddenInput,
    )
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
        min_value=Decimal("0.001"),
        decimal_places=3,
        max_digits=12,
        widget=forms.NumberInput(attrs={**_TEXT, "step": "0.001"}),
    )
    unit_price = forms.DecimalField(
        label="قیمت واحد (ریال)",
        required=False,
        min_value=1,
        decimal_places=0,
        max_digits=14,
        widget=forms.NumberInput(attrs={**_TEXT, "inputmode": "numeric"}),
    )

    def __init__(self, *args, business=None, **kwargs):
        super().__init__(*args, **kwargs)
        item_qs = InventoryLot.objects.none()
        if business is not None:
            from apps.inventory.selectors import lots_for_business

            item_qs = lots_for_business(business)
        self.fields["item"].queryset = item_qs

        raw_item = self.initial.get("item") or self.data.get(self.add_prefix("item"))
        try:
            selected = item_qs.filter(pk=raw_item).first() if raw_item else None
        except (DjangoValidationError, TypeError, ValueError):
            selected = None
        self.item_label = str(selected) if selected else ""

    def clean(self):
        cleaned = super().clean()
        # A completely empty row is a spare line the user did not fill in, not an
        # error. A partly-filled one is a mistake worth reporting.
        filled = any(
            cleaned.get(key) not in (None, "")
            for key in ("item", "product_name", "quantity", "unit_price")
        )
        if not filled:
            return cleaned
        if not cleaned.get("item") and not (cleaned.get("product_name") or "").strip():
            self.add_error("product_name", "محصول را انتخاب کنید یا نام آن را وارد کنید.")
        if cleaned.get("quantity") in (None, ""):
            self.add_error("quantity", "مقدار را وارد کنید.")
        if cleaned.get("unit_price") in (None, ""):
            self.add_error("unit_price", "قیمت را وارد کنید.")
        return cleaned

class BaseInvoiceLineFormSet(forms.BaseFormSet):
    def __init__(self, *args, business=None, **kwargs):
        self.business = business
        super().__init__(*args, **kwargs)

    def get_form_kwargs(self, index):
        return {**super().get_form_kwargs(index), "business": self.business}


InvoiceLineFormSet = forms.formset_factory(
    InvoiceLineForm,
    formset=BaseInvoiceLineFormSet,
    extra=3,
    can_delete=True,
)
