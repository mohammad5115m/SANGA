from __future__ import annotations

from django import forms

from apps.pricing.models import LotPrice

from .filters import SORT_CHOICES, ItemFilterSpec
from .models import Application, InventoryLot, Product
from .selectors import OWNER_STATE_CHOICES

_TEXT = {"class": "field-input"}
_CHECK = {"class": "field-checkbox"}


def active_applications():
    return Application.objects.filter(is_active=True)


class ProductPickForm(forms.Form):
    """Step 1 — which product is this, in the catalogue sense."""

    product = forms.ModelChoiceField(
        label="محصول موجود",
        queryset=Product.objects.none(),
        required=False,
        widget=forms.Select(attrs=_TEXT),
    )
    commercial_name = forms.CharField(
        label="یا نام محصول جدید",
        required=False,
        max_length=200,
        widget=forms.TextInput(attrs={**_TEXT, "placeholder": "مثلاً تراورتن عباس‌آباد"}),
    )
    stone_type = forms.CharField(
        label="نوع سنگ",
        required=False,
        max_length=100,
        widget=forms.TextInput(attrs={**_TEXT, "placeholder": "تراورتن"}),
    )
    primary_color = forms.CharField(
        label="رنگ غالب",
        required=False,
        max_length=100,
        widget=forms.TextInput(attrs={**_TEXT, "placeholder": "کرم"}),
    )
    quarry_region = forms.CharField(
        label="معدن/منطقه",
        required=False,
        max_length=150,
        widget=forms.TextInput(attrs=_TEXT),
    )
    applications = forms.ModelMultipleChoiceField(
        label="کاربردها",
        queryset=Application.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs=_CHECK),
    )

    def __init__(self, *args, business=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["applications"].queryset = active_applications()
        if business is not None:
            self.fields["product"].queryset = Product.objects.filter(business=business, is_active=True)

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("product") and not (cleaned.get("commercial_name") or "").strip():
            raise forms.ValidationError("یک محصول انتخاب کنید یا نام محصول جدید را وارد کنید.")
        return cleaned


class ItemDetailsForm(forms.Form):
    """Step 2 — what distinguishes this sellable instance, and where it is."""

    lot_code = forms.CharField(
        label="کد محصول (اختیاری)",
        required=False,
        max_length=64,
        widget=forms.TextInput(attrs={**_TEXT, "placeholder": "خالی بماند = خودکار"}),
    )
    grade = forms.CharField(
        label="سورت/درجه",
        required=False,
        max_length=50,
        widget=forms.TextInput(attrs=_TEXT),
    )
    processing_type = forms.CharField(
        label="نوع فرآوری",
        required=False,
        max_length=100,
        widget=forms.TextInput(attrs={**_TEXT, "placeholder": "صیقلی / هوند"}),
    )
    length_cm = forms.DecimalField(
        label="طول (cm)", required=False, min_value=0, decimal_places=2, max_digits=8,
        widget=forms.NumberInput(attrs={**_TEXT, "step": "0.01"}),
    )
    width_cm = forms.DecimalField(
        label="عرض (cm)", required=False, min_value=0, decimal_places=2, max_digits=8,
        widget=forms.NumberInput(attrs={**_TEXT, "step": "0.01"}),
    )
    thickness_mm = forms.DecimalField(
        label="ضخامت (mm)", required=False, min_value=0, decimal_places=2, max_digits=8,
        widget=forms.NumberInput(attrs={**_TEXT, "step": "0.01"}),
    )
    slab_count = forms.IntegerField(
        label="تعداد اسلب", required=False, min_value=0,
        widget=forms.NumberInput(attrs={**_TEXT, "inputmode": "numeric"}),
    )
    location_province = forms.CharField(
        label="استان", required=False, max_length=100, widget=forms.TextInput(attrs=_TEXT)
    )
    location_city = forms.CharField(
        label="شهر", required=False, max_length=100, widget=forms.TextInput(attrs=_TEXT)
    )
    location_address = forms.CharField(
        label="آدرس دقیق (فقط برای شما)",
        required=False,
        widget=forms.Textarea(attrs={**_TEXT, "rows": 2}),
        help_text="آدرس دقیق هرگز به مشتری عمومی نمایش داده نمی‌شود.",
    )
    description = forms.CharField(
        label="توضیح", required=False, widget=forms.Textarea(attrs={**_TEXT, "rows": 3})
    )


class ItemStockForm(forms.Form):
    """Step 3a — how much, and how long we vouch for it."""

    stock_mode = forms.ChoiceField(
        label="نوع موجودی",
        choices=InventoryLot.StockMode.choices,
        initial=InventoryLot.StockMode.EXACT,
        widget=forms.RadioSelect(attrs=_CHECK),
    )
    available_sqm = forms.DecimalField(
        label="متراژ موجود (m²)",
        required=False,
        min_value=0,
        decimal_places=3,
        max_digits=12,
        widget=forms.NumberInput(attrs={**_TEXT, "step": "0.001", "inputmode": "decimal"}),
    )
    stock_valid_for_days = forms.IntegerField(
        label="این موجودی تا چند روز معتبر است؟",
        min_value=1,
        max_value=365,
        initial=7,
        widget=forms.NumberInput(attrs={**_TEXT, "inputmode": "numeric"}),
    )

    def clean(self):
        cleaned = super().clean()
        # A quantity is only meaningful in exact mode; requiring one in the other
        # modes is what pushes sellers into typing a fake number.
        if cleaned.get("stock_mode") == InventoryLot.StockMode.EXACT and cleaned.get("available_sqm") is None:
            self.add_error("available_sqm", "متراژ موجود را وارد کنید یا نوع موجودی دیگری انتخاب کنید.")
        return cleaned


class TierPriceForm(forms.Form):
    """One audience's price. Instantiated twice — once for B2B, once for B2C.

    Keeping the two channels as two separate form instances rather than one form
    with `b2b_*`/`b2c_*` field pairs means neither can accidentally read the
    other's POST data.
    """

    mode = forms.ChoiceField(
        label="نوع قیمت",
        choices=LotPrice.Mode.choices,
        initial=LotPrice.Mode.FIXED,
        widget=forms.Select(attrs=_TEXT),
    )
    amount = forms.DecimalField(
        label="مبلغ (ریال)",
        required=False,
        min_value=0,
        decimal_places=0,
        max_digits=14,
        widget=forms.NumberInput(attrs={**_TEXT, "inputmode": "numeric"}),
    )
    valid_for_days = forms.IntegerField(
        label="اعتبار قیمت (روز)",
        min_value=1,
        max_value=365,
        initial=7,
        widget=forms.NumberInput(attrs={**_TEXT, "inputmode": "numeric"}),
    )
    special_amount = forms.DecimalField(
        label="قیمت فروش ویژه (ریال)",
        required=False,
        min_value=0,
        decimal_places=0,
        max_digits=14,
        widget=forms.NumberInput(attrs={**_TEXT, "inputmode": "numeric"}),
    )
    special_until = forms.DateTimeField(
        label="پایان فروش ویژه",
        required=False,
        widget=forms.DateTimeInput(attrs={**_TEXT, "type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
    )

    def __init__(self, *args, tier_label: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.tier_label = tier_label
        self.fields["special_until"].input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("mode") == LotPrice.Mode.FIXED and cleaned.get("amount") is None:
            self.add_error("amount", "مبلغ را وارد کنید یا «استعلام قیمت» را انتخاب کنید.")
        if cleaned.get("special_amount") is not None and cleaned.get("mode") != LotPrice.Mode.FIXED:
            self.add_error("special_amount", "فروش ویژه فقط برای قیمت مشخص ممکن است.")
        return cleaned


class ItemVisibilityForm(forms.Form):
    """Step 4 — publish or keep internal."""

    is_visible = forms.BooleanField(
        label="نمایش به همکاران و مشتریان",
        required=False,
        widget=forms.CheckboxInput(attrs=_CHECK),
        help_text="با فعال کردن این گزینه، محصول در بازار همکاران و جستجوی عمومی دیده می‌شود.",
    )
    is_urgent_sale = forms.BooleanField(
        label="فروش فوری", required=False, widget=forms.CheckboxInput(attrs=_CHECK)
    )


class ItemMediaForm(forms.Form):
    images = forms.FileField(
        label="عکس / ویدیو",
        required=False,
        widget=forms.ClearableFileInput(attrs={**_TEXT, "accept": "image/*,video/*"}),
    )
    is_primary = forms.BooleanField(
        label="تصویر اصلی", required=False, initial=True, widget=forms.CheckboxInput(attrs=_CHECK)
    )


class ItemEditForm(forms.ModelForm):
    class Meta:
        model = InventoryLot
        fields = (
            "grade",
            "processing_type",
            "stock_mode",
            "available_sqm",
            "stock_valid_for_days",
            "slab_count",
            "length_cm",
            "width_cm",
            "thickness_mm",
            "location_province",
            "location_city",
            "location_address",
            "description",
            "defect_notes",
            "is_visible",
            "availability_status",
            "is_urgent_sale",
        )
        widgets = {
            "grade": forms.TextInput(attrs=_TEXT),
            "processing_type": forms.TextInput(attrs=_TEXT),
            "stock_mode": forms.Select(attrs=_TEXT),
            "available_sqm": forms.NumberInput(attrs={**_TEXT, "step": "0.001"}),
            "stock_valid_for_days": forms.NumberInput(attrs=_TEXT),
            "slab_count": forms.NumberInput(attrs=_TEXT),
            "length_cm": forms.NumberInput(attrs={**_TEXT, "step": "0.01"}),
            "width_cm": forms.NumberInput(attrs={**_TEXT, "step": "0.01"}),
            "thickness_mm": forms.NumberInput(attrs={**_TEXT, "step": "0.01"}),
            "location_province": forms.TextInput(attrs=_TEXT),
            "location_city": forms.TextInput(attrs=_TEXT),
            "location_address": forms.Textarea(attrs={**_TEXT, "rows": 2}),
            "description": forms.Textarea(attrs={**_TEXT, "rows": 3}),
            "defect_notes": forms.Textarea(attrs={**_TEXT, "rows": 2}),
            "is_visible": forms.CheckboxInput(attrs=_CHECK),
            "availability_status": forms.Select(attrs=_TEXT),
            "is_urgent_sale": forms.CheckboxInput(attrs=_CHECK),
        }

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("stock_mode") == InventoryLot.StockMode.EXACT and cleaned.get("available_sqm") is None:
            self.add_error("available_sqm", "متراژ موجود را وارد کنید یا نوع موجودی دیگری انتخاب کنید.")
        return cleaned


class ItemFilterForm(forms.Form):
    """The one search form, reused by «موجودی من», the marketplace and public search.

    Its whole job is to validate GET input and hand back an
    :class:`~apps.inventory.filters.ItemFilterSpec`, so no surface has to invent
    its own filter vocabulary.
    """

    q = forms.CharField(
        required=False,
        label="جستجو",
        widget=forms.TextInput(attrs={**_TEXT, "placeholder": "نام سنگ، رنگ، معدن، کد..."}),
    )
    stone_type = forms.CharField(
        required=False, label="نوع سنگ", widget=forms.TextInput(attrs={**_TEXT, "placeholder": "تراورتن"})
    )
    color = forms.CharField(
        required=False, label="رنگ", widget=forms.TextInput(attrs={**_TEXT, "placeholder": "کرم"})
    )
    quarry_region = forms.CharField(
        required=False, label="معدن/منطقه", widget=forms.TextInput(attrs=_TEXT)
    )
    processing_type = forms.CharField(
        required=False, label="فرآوری", widget=forms.TextInput(attrs=_TEXT)
    )
    grade = forms.CharField(required=False, label="سورت", widget=forms.TextInput(attrs=_TEXT))
    applications = forms.ModelMultipleChoiceField(
        label="کاربرد",
        queryset=Application.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs=_CHECK),
    )
    thickness_min = forms.DecimalField(
        required=False, label="حداقل ضخامت", min_value=0, widget=forms.NumberInput(attrs=_TEXT)
    )
    thickness_max = forms.DecimalField(
        required=False, label="حداکثر ضخامت", min_value=0, widget=forms.NumberInput(attrs=_TEXT)
    )
    price_min = forms.DecimalField(
        required=False, label="حداقل قیمت", min_value=0, widget=forms.NumberInput(attrs=_TEXT)
    )
    price_max = forms.DecimalField(
        required=False, label="حداکثر قیمت", min_value=0, widget=forms.NumberInput(attrs=_TEXT)
    )
    min_qty_sqm = forms.DecimalField(
        required=False, label="حداقل متراژ", min_value=0, widget=forms.NumberInput(attrs=_TEXT)
    )
    stock_mode = forms.ChoiceField(
        required=False,
        label="موجودی",
        choices=[("", "همه")] + list(InventoryLot.StockMode.choices),
        widget=forms.Select(attrs=_TEXT),
    )
    only_special = forms.BooleanField(
        required=False, label="فقط فروش ویژه", widget=forms.CheckboxInput(attrs=_CHECK)
    )
    sort = forms.ChoiceField(
        required=False, label="ترتیب", choices=SORT_CHOICES, widget=forms.Select(attrs=_TEXT)
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["applications"].queryset = active_applications()

    def to_spec(self) -> ItemFilterSpec:
        """Never raises: an unusable filter degrades to "no filter"."""
        if not self.is_valid():
            return ItemFilterSpec()
        data = dict(self.cleaned_data)
        data["applications"] = [app.code for app in data.get("applications") or []]
        return ItemFilterSpec.from_dict(data)


class OwnerItemFilterForm(ItemFilterForm):
    """Seller-side search, plus the lifecycle filter only an owner may use."""

    state = forms.ChoiceField(
        required=False,
        label="وضعیت",
        choices=OWNER_STATE_CHOICES,
        widget=forms.Select(attrs=_TEXT),
    )

    @property
    def state_value(self) -> str:
        return self.cleaned_data.get("state", "") if self.is_valid() else ""
