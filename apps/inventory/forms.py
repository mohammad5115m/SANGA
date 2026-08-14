from __future__ import annotations

from decimal import Decimal

from django import forms
from django.utils import timezone

from apps.pricing.models import LotPrice

from .filters import SORT_CHOICES, ItemFilterSpec
from .models import Application, InventoryLot, VocabularyTerm
from .selectors import OWNER_STATE_CHOICES

_TEXT = {"class": "field-input"}
_CHECK = {"class": "field-checkbox"}


def active_applications():
    return Application.objects.filter(is_active=True)


def active_stones():
    return VocabularyTerm.objects.filter(
        kind=VocabularyTerm.Kind.STONE_TYPE,
        is_active=True,
    ).order_by("sort_order", "name")


class ProductItemForm(forms.Form):
    """The single product form used for both creation and editing."""

    stone = forms.ModelChoiceField(
        label="نوع سنگ",
        queryset=VocabularyTerm.objects.none(),
        widget=forms.Select(attrs=_TEXT),
    )
    name_suffix = forms.CharField(
        label="نام تکمیلی",
        required=False,
        max_length=160,
        widget=forms.TextInput(attrs={**_TEXT, "placeholder": "مثلاً عباس‌آباد موج‌دار"}),
        help_text="بخش «سنگ + نوع سنگ» خودکار ساخته می‌شود و قابل ویرایش نیست.",
    )
    applications = forms.ModelMultipleChoiceField(
        label="کاربردها",
        queryset=Application.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs=_CHECK),
    )
    pattern = forms.CharField(
        label="طرح/بافت", required=False, max_length=150, widget=forms.TextInput(attrs=_TEXT)
    )
    processing_type = forms.CharField(
        label="نوع فرآوری",
        required=False,
        max_length=100,
        initial="ساب خورده",
        widget=forms.TextInput(
            attrs={**_TEXT, "placeholder": "ساب خورده", "list": "seller-processing-suggestions"}
        ),
    )
    length_cm = forms.DecimalField(
        label="طول (سانتی‌متر)",
        required=False,
        min_value=Decimal("0.01"),
        decimal_places=2,
        max_digits=8,
        widget=forms.NumberInput(attrs={**_TEXT, "step": "0.01", "placeholder": "خالی = آزاد"}),
        help_text="اگر طول آزاد است، این فیلد را خالی بگذارید.",
    )
    width_cm = forms.DecimalField(
        label="عرض (سانتی‌متر)",
        required=False,
        min_value=Decimal("0.01"),
        decimal_places=2,
        max_digits=8,
        widget=forms.NumberInput(attrs={**_TEXT, "step": "0.01"}),
    )
    thickness_cm = forms.DecimalField(
        label="ضخامت (سانتی‌متر)",
        required=False,
        min_value=Decimal("0.01"),
        decimal_places=2,
        max_digits=6,
        initial=Decimal("2"),
        widget=forms.NumberInput(attrs={**_TEXT, "step": "0.1"}),
    )
    available_sqm = forms.DecimalField(
        label="متراژ موجود (متر مربع)",
        required=False,
        min_value=0,
        decimal_places=3,
        max_digits=12,
        widget=forms.NumberInput(attrs={**_TEXT, "step": "0.001", "inputmode": "decimal"}),
        help_text="خالی بماند = استعلام موجودی.",
    )
    stock_valid_for_days = forms.IntegerField(
        label="اعتبار موجودی (روز)",
        min_value=1,
        max_value=365,
        initial=7,
        widget=forms.NumberInput(attrs={**_TEXT, "inputmode": "numeric"}),
    )
    min_sale_qty = forms.DecimalField(
        label="حداقل فروش (متر مربع)",
        required=False,
        min_value=0,
        decimal_places=3,
        max_digits=12,
        widget=forms.NumberInput(attrs={**_TEXT, "step": "0.001"}),
    )
    description_public = forms.CharField(
        label="توضیح برای مشتری", required=False, widget=forms.Textarea(attrs={**_TEXT, "rows": 3})
    )
    description_professional = forms.CharField(
        label="توضیح حرفه‌ای", required=False, widget=forms.Textarea(attrs={**_TEXT, "rows": 3})
    )
    defect_notes = forms.CharField(
        label="نکات و ایرادها", required=False, widget=forms.Textarea(attrs={**_TEXT, "rows": 2})
    )
    availability_status = forms.ChoiceField(
        label="وضعیت موجود بودن",
        choices=InventoryLot.Availability.choices,
        initial=InventoryLot.Availability.AVAILABLE,
        widget=forms.Select(attrs=_TEXT),
    )
    is_visible = forms.BooleanField(
        label="نمایش به همکاران و مشتریان", required=False, widget=forms.CheckboxInput(attrs=_CHECK)
    )
    is_urgent_sale = forms.BooleanField(
        label="فروش فوری", required=False, widget=forms.CheckboxInput(attrs=_CHECK)
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["stone"].queryset = active_stones()
        self.fields["applications"].queryset = active_applications()

    def clean_name_suffix(self):
        return " ".join((self.cleaned_data["name_suffix"] or "").split())

    def clean_processing_type(self):
        return " ".join((self.cleaned_data["processing_type"] or "ساب خورده").split())

    @property
    def thickness_mm(self):
        value = self.cleaned_data.get("thickness_cm")
        return value * Decimal("10") if value is not None else None


class ItemStockForm(forms.Form):
    available_sqm = forms.DecimalField(
        label="متراژ موجود (متر مربع)",
        required=False,
        min_value=0,
        decimal_places=3,
        max_digits=12,
        widget=forms.NumberInput(attrs={**_TEXT, "step": "0.001"}),
        help_text="خالی بماند = استعلام موجودی.",
    )
    stock_valid_for_days = forms.IntegerField(
        label="اعتبار موجودی (روز)", min_value=1, max_value=365, initial=7,
        widget=forms.NumberInput(attrs=_TEXT),
    )


class TierPriceForm(forms.Form):
    mode = forms.ChoiceField(
        label="نوع قیمت", choices=LotPrice.Mode.choices, initial=LotPrice.Mode.FIXED,
        widget=forms.Select(attrs=_TEXT),
    )
    amount = forms.DecimalField(
        label="مبلغ هر متر مربع (ریال)", required=False, min_value=1, decimal_places=0,
        max_digits=14, widget=forms.NumberInput(attrs={**_TEXT, "inputmode": "numeric"}),
    )
    valid_for_days = forms.IntegerField(
        label="اعتبار قیمت (روز)", min_value=1, max_value=365, initial=7,
        widget=forms.NumberInput(attrs=_TEXT),
    )
    special_amount = forms.DecimalField(
        label="قیمت فروش ویژه (ریال)", required=False, min_value=1, decimal_places=0,
        max_digits=14, widget=forms.NumberInput(attrs={**_TEXT, "inputmode": "numeric"}),
    )
    special_until = forms.DateTimeField(
        label="پایان فروش ویژه", required=False,
        widget=forms.DateTimeInput(attrs={**_TEXT, "type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
    )

    def __init__(self, *args, tier_label: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.tier_label = tier_label
        self.fields["special_until"].input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]

    def clean(self):
        cleaned = super().clean()
        mode = cleaned.get("mode")
        amount = cleaned.get("amount")
        special_amount = cleaned.get("special_amount")
        special_until = cleaned.get("special_until")
        if mode == LotPrice.Mode.FIXED and amount is None:
            self.add_error("amount", "مبلغ را وارد کنید یا «استعلام قیمت» را انتخاب کنید.")
        if mode == LotPrice.Mode.INQUIRY and (special_amount is not None or special_until is not None):
            self.add_error("special_amount", "فروش ویژه فقط برای قیمت مشخص ممکن است.")
        if (special_amount is None) != (special_until is None):
            self.add_error("special_amount", "مبلغ و زمان پایان فروش ویژه باید با هم وارد شوند.")
        if special_amount is not None and amount is not None and special_amount >= amount:
            self.add_error("special_amount", "قیمت فروش ویژه باید کمتر از قیمت عادی باشد.")
        if special_until is not None and special_until <= timezone.now():
            self.add_error("special_until", "زمان پایان فروش ویژه باید در آینده باشد.")
        return cleaned


class ItemMediaForm(forms.Form):
    images = forms.FileField(
        label="عکس / ویدیو", required=False,
        widget=forms.ClearableFileInput(attrs={**_TEXT, "accept": "image/*,video/*"}),
    )
    is_primary = forms.BooleanField(
        label="تصویر اصلی", required=False, initial=True, widget=forms.CheckboxInput(attrs=_CHECK)
    )


class ItemFilterForm(forms.Form):
    q = forms.CharField(
        required=False, label="جستجو",
        widget=forms.TextInput(attrs={**_TEXT, "placeholder": "نام، کد یا فرآوری..."}),
    )
    stone = forms.ModelChoiceField(
        required=False, label="نوع سنگ", queryset=VocabularyTerm.objects.none(),
        empty_label="همه", widget=forms.Select(attrs=_TEXT),
    )
    processing_type = forms.CharField(required=False, label="فرآوری", widget=forms.TextInput(attrs=_TEXT))
    applications = forms.ModelMultipleChoiceField(
        label="کاربرد", queryset=Application.objects.none(), required=False,
        widget=forms.CheckboxSelectMultiple(attrs=_CHECK),
    )
    availability = forms.ChoiceField(
        required=False, label="موجود بودن",
        choices=[("", "همه")] + list(InventoryLot.Availability.choices),
        widget=forms.Select(attrs=_TEXT),
    )
    price_min = forms.DecimalField(
        required=False, label="حداقل قیمت", min_value=0, widget=forms.NumberInput(attrs=_TEXT)
    )
    price_max = forms.DecimalField(
        required=False, label="حداکثر قیمت", min_value=0, widget=forms.NumberInput(attrs=_TEXT)
    )
    sort = forms.ChoiceField(
        required=False, label="ترتیب", choices=SORT_CHOICES, widget=forms.Select(attrs=_TEXT)
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["stone"].queryset = active_stones()
        self.fields["applications"].queryset = active_applications()

    def clean(self):
        cleaned = super().clean()
        low, high = cleaned.get("price_min"), cleaned.get("price_max")
        if low is not None and high is not None and low > high:
            self.add_error("price_max", "حداکثر قیمت باید از حداقل قیمت بیشتر باشد.")
        return cleaned

    def to_spec(self) -> ItemFilterSpec:
        if not self.is_valid():
            return ItemFilterSpec()
        data = dict(self.cleaned_data)
        stone = data.get("stone")
        data["stone"] = str(stone.pk) if stone else ""
        data["applications"] = [app.code for app in data.get("applications") or []]
        return ItemFilterSpec.from_dict(data)


class OwnerItemFilterForm(ItemFilterForm):
    state = forms.ChoiceField(
        required=False, label="وضعیت", choices=OWNER_STATE_CHOICES, widget=forms.Select(attrs=_TEXT)
    )

    @property
    def state_value(self) -> str:
        return self.cleaned_data.get("state", "") if self.is_valid() else ""
