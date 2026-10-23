from __future__ import annotations

from decimal import Decimal

from django import forms

from apps.core.forms import PersianNumericFormMixin
from apps.core.persian import normalize_persian_text

from .filters import SORT_CHOICES, ItemFilterSpec
from .models import Application, InventoryLot, Product, VocabularyTerm
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


def _mobile_numeric_widgets(form):
    # NumberInput discards Persian digits and malformed values in browsers,
    # making it impossible to correct the original input after an error.
    for name in form.numeric_fields:
        field = form.fields[name]
        attrs = {key: value for key, value in field.widget.attrs.items() if key != "step"}
        integer = getattr(field, "decimal_places", 0) == 0
        attrs.update(dir="ltr", inputmode="numeric" if integer else "decimal")
        field.widget = forms.TextInput(attrs=attrs)


class StoneChoiceField(forms.ModelChoiceField):
    """Render the value itself, without the model's admin-oriented kind prefix."""

    def label_from_instance(self, obj):
        return obj.name


class ProductItemForm(PersianNumericFormMixin, forms.Form):
    """The single product form used for both creation and editing."""

    numeric_fields = (
        "length_cm",
        "width_cm",
        "thickness_cm",
        "available_sqm",
        "stock_valid_for_days",
        "min_sale_qty",
    )

    submission_id = forms.UUIDField(label="شناسه ثبت", required=False, widget=forms.HiddenInput)

    stone = StoneChoiceField(
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
        required=True,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "application-checkbox"}),
        help_text="حداقل یک کاربرد را انتخاب کنید؛ می‌توانید چند مورد را برگزینید.",
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
        help_text="برای محصول با طول آزاد، این فیلد را خالی بگذارید.",
    )
    width_cm = forms.DecimalField(
        label="عرض (سانتی‌متر)",
        required=True,
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
        label="توضیح برای مشتری",
        required=False,
        widget=forms.Textarea(
            attrs={**_TEXT, "rows": 3, "placeholder": "ویژگی‌ها و اطلاعات مفید برای تمام خریداران"}
        ),
    )
    description_colleague = forms.CharField(
        label="توضیح برای همکار",
        required=False,
        widget=forms.Textarea(
            attrs={**_TEXT, "rows": 3, "placeholder": "اطلاعات تکمیلی ویژه خریداران همکار"}
        ),
    )
    description_private = forms.CharField(
        label="توضیح شخصی",
        required=False,
        widget=forms.Textarea(
            attrs={**_TEXT, "rows": 3, "placeholder": "یادداشت خصوصی؛ فقط مالک کسب‌وکار"}
        ),
    )
    private_address = forms.CharField(
        label="آدرس محصول",
        required=False,
        widget=forms.Textarea(
            attrs={**_TEXT, "rows": 2, "autocomplete": "street-address", "placeholder": "آدرس محل نگهداری یا بارگیری"}
        ),
        help_text="این آدرس فقط برای مالک کسب‌وکار نمایش داده می‌شود.",
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

    def __init__(self, *args, include_private: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        _mobile_numeric_widgets(self)
        self.fields["stone"].queryset = active_stones()
        self.fields["applications"].queryset = active_applications()
        if not include_private:
            self.fields.pop("description_private", None)
            self.fields.pop("private_address", None)
        self.fields["available_sqm"].help_text = "خالی = استعلام موجودی؛ صفر فقط برای محصول ناموجود."
        self.fields["min_sale_qty"].help_text = "اختیاری؛ نباید بیشتر از متراژ موجود باشد."

    def clean_name_suffix(self):
        return normalize_persian_text(self.cleaned_data["name_suffix"] or "")

    def clean_processing_type(self):
        return normalize_persian_text(self.cleaned_data["processing_type"] or "ساب خورده")

    def clean(self):
        cleaned = super().clean()
        quantity = cleaned.get("available_sqm")
        minimum = cleaned.get("min_sale_qty") or Decimal("0")
        availability = cleaned.get("availability_status")
        stone = cleaned.get("stone")
        if stone:
            name = " ".join(part for part in ("سنگ", stone.name, cleaned.get("name_suffix", "")) if part)
            if len(name) > Product._meta.get_field("commercial_name").max_length:
                self.add_error("name_suffix", "نام کامل محصول طولانی است؛ نام تکمیلی را کوتاه‌تر کنید.")

        if quantity == 0 and availability == InventoryLot.Availability.AVAILABLE:
            self.add_error("available_sqm", "محصول با موجودی صفر باید «ناموجود» باشد.")
        if quantity is not None and minimum > quantity:
            self.add_error("min_sale_qty", "حداقل فروش نمی‌تواند از موجودی بیشتر باشد.")
        return cleaned

    @property
    def thickness_mm(self):
        value = self.cleaned_data.get("thickness_cm")
        return value * Decimal("10") if value is not None else None


class ItemStockForm(PersianNumericFormMixin, forms.Form):
    numeric_fields = ("available_sqm", "stock_valid_for_days")
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


class TierPriceForm(PersianNumericFormMixin, forms.Form):
    numeric_fields = ("amount", "valid_for_days", "special_amount", "special_valid_for_days")
    amount = forms.DecimalField(
        label="مبلغ هر متر مربع (ریال)", required=False, min_value=1, decimal_places=0,
        max_digits=14, widget=forms.NumberInput(attrs={**_TEXT, "inputmode": "numeric"}),
        help_text="اگر خالی بماند، «استعلام قیمت» نمایش داده می‌شود.",
    )
    valid_for_days = forms.IntegerField(
        label="اعتبار قیمت (روز)", min_value=1, max_value=365, initial=7,
        widget=forms.NumberInput(attrs=_TEXT),
    )
    is_special = forms.BooleanField(
        label="فروش ویژه", required=False, widget=forms.CheckboxInput(attrs=_CHECK),
        help_text="با فعال‌کردن، مبلغ و مدت اعتبار فروش ویژه را وارد کنید.",
    )
    special_amount = forms.DecimalField(
        label="قیمت فروش ویژه (ریال)", required=False, min_value=1, decimal_places=0,
        max_digits=14, widget=forms.NumberInput(attrs={**_TEXT, "inputmode": "numeric"}),
    )
    special_valid_for_days = forms.IntegerField(
        label="مدت اعتبار فروش ویژه (روز)", required=False, min_value=1, max_value=365,
        widget=forms.NumberInput(attrs={**_TEXT, "inputmode": "numeric"}),
    )

    def __init__(self, *args, tier_label: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.tier_label = tier_label
        _mobile_numeric_widgets(self)
        if self.is_bound and not self.fields["is_special"].widget.value_from_datadict(
            self.data, self.files, self.add_prefix("is_special")
        ):
            # Ignore stale promotion input before field validation, including
            # malformed values submitted by an older tab with the toggle off.
            data = self.data.copy()
            data[self.add_prefix("special_amount")] = ""
            data[self.add_prefix("special_valid_for_days")] = ""
            self.data = data

    def clean(self):
        cleaned = super().clean()
        amount = cleaned.get("amount")
        is_special = cleaned.get("is_special")
        special_amount = cleaned.get("special_amount")
        days = cleaned.get("special_valid_for_days")
        price_days = cleaned.get("valid_for_days")
        if is_special:
            if amount is None:
                self.add_error("amount", "برای فروش ویژه، مبلغ هر متر مربع را وارد کنید.")
            if special_amount is None and "special_amount" not in self.errors:
                self.add_error("special_amount", "قیمت فروش ویژه را وارد کنید.")
            if days is None and "special_valid_for_days" not in self.errors:
                self.add_error("special_valid_for_days", "مدت اعتبار فروش ویژه را وارد کنید.")
            if days is not None and price_days is not None and days > price_days:
                self.add_error("special_valid_for_days", "مدت فروش ویژه نباید از اعتبار قیمت بیشتر باشد.")
            if special_amount is not None and amount is not None and special_amount >= amount:
                self.add_error("special_amount", "قیمت فروش ویژه باید کمتر از قیمت عادی باشد.")
        else:
            # A disabled promotion cannot survive a price edit, even if stale
            # hidden values were submitted by an older browser tab.
            cleaned["special_amount"] = None
            cleaned["special_valid_for_days"] = None
        return cleaned


class MultipleMediaInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleMediaField(forms.FileField):
    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else [data] if data else []
        if not files:
            return super().clean(None, initial)
        if len(files) > 10:
            raise forms.ValidationError("در هر بار حداکثر ۱۰ فایل انتخاب کنید.")
        return [super(MultipleMediaField, self).clean(file, initial) for file in files]


class ItemMediaForm(forms.Form):
    images = MultipleMediaField(
        label="عکس و ویدیو", required=True,
        widget=MultipleMediaInput(attrs={
            "class": "media-upload-input", "accept": ".jpg,.jpeg,.png,.webp,.gif,.mp4,.mov,.webm", "multiple": True,
        }),
    )
    is_primary = forms.BooleanField(
        label="تصویر اصلی", required=False, initial=True, widget=forms.CheckboxInput(attrs=_CHECK)
    )


class ItemFilterForm(PersianNumericFormMixin, forms.Form):
    numeric_fields = ("price_min", "price_max", "min_qty_sqm")
    q = forms.CharField(
        required=False, label="جست‌وجو", max_length=100,
        widget=forms.TextInput(attrs={**_TEXT, "placeholder": "نام سنگ، کد، کاربرد یا فرآوری..."}),
    )
    stone = StoneChoiceField(
        required=False, label="نوع سنگ", queryset=VocabularyTerm.objects.none(),
        empty_label="همه", widget=forms.Select(attrs=_TEXT),
    )
    processing_type = forms.CharField(
        required=False,
        label="فرآوری",
        max_length=100,
        widget=forms.TextInput(attrs={**_TEXT, "placeholder": "مثلاً صیقلی"}),
    )
    applications = forms.ModelMultipleChoiceField(
        label="کاربرد", queryset=Application.objects.none(), required=False,
        widget=forms.CheckboxSelectMultiple(attrs=_CHECK),
    )
    application_match = forms.ChoiceField(
        required=False,
        label="تطبیق کاربردها",
        choices=(("any", "حداقل یکی"), ("all", "همه کاربردهای انتخاب‌شده")),
        initial="any",
        widget=forms.Select(attrs=_TEXT),
    )
    availability = forms.ChoiceField(
        required=False, label="موجود بودن",
        choices=[("", "همه")] + list(InventoryLot.Availability.choices),
        widget=forms.Select(attrs=_TEXT),
    )
    price_min = forms.DecimalField(
        required=False,
        label="حداقل قیمت (ریال)",
        min_value=0,
        max_digits=14,
        decimal_places=0,
        widget=forms.NumberInput(attrs={**_TEXT, "inputmode": "numeric"}),
    )
    price_max = forms.DecimalField(
        required=False,
        label="حداکثر قیمت (ریال)",
        min_value=0,
        max_digits=14,
        decimal_places=0,
        widget=forms.NumberInput(attrs={**_TEXT, "inputmode": "numeric"}),
    )
    min_qty_sqm = forms.DecimalField(
        required=False,
        label="حداقل متراژ موجود",
        min_value=0,
        max_digits=12,
        decimal_places=3,
        widget=forms.NumberInput(attrs={**_TEXT, "step": "0.001", "inputmode": "decimal"}),
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
        # ``cleaned_data`` still contains every valid field when one other field
        # is invalid. Preserve those filters and show the bad field's error;
        # silently returning the whole inventory is the unsafe alternative.
        if not self.is_bound:
            return ItemFilterSpec()
        self.is_valid()
        data = dict(self.cleaned_data)
        stone = data.get("stone")
        data["stone"] = str(stone.pk) if stone else ""
        # Keep old shared URLs and saved browser bookmarks working without
        # putting the retired free-text controls back into the compact UI.
        if not stone and self.data.get("stone_type"):
            data["stone_type"] = self.data.get("stone_type")
        data["applications"] = [app.code for app in data.get("applications") or []]
        return ItemFilterSpec.from_dict(data)

    @property
    def advanced_scalar_fields(self):
        names = [
            "stone",
            "processing_type",
            "availability",
            "price_min",
            "price_max",
            "min_qty_sqm",
            "sort",
            "application_match",
        ]
        return [self[name] for name in names]

    @property
    def has_advanced_values(self) -> bool:
        keys = {
            "stone",
            "processing_type",
            "applications",
            "availability",
            "price_min",
            "price_max",
            "min_qty_sqm",
            "sort",
            "state",
            "price_tier",
            "application_match",
        }
        return any(
            self.data.getlist(key) if hasattr(self.data, "getlist") else self.data.get(key)
            for key in keys
        )

    @property
    def active_filter_count(self) -> int:
        if not self.is_bound:
            return 0
        self.is_valid()
        data = self.cleaned_data
        count = sum(
            data.get(key) is not None if key in {"price_min", "price_max", "min_qty_sqm"} else bool(data.get(key))
            for key in (
                "q",
                "stone",
                "processing_type",
                "applications",
                "availability",
                "price_min",
                "price_max",
                "min_qty_sqm",
                "state",
                "price_tier",
            )
        )
        if data.get("sort") not in (None, "", "recent"):
            count += 1
        if data.get("application_match") == "all":
            count += 1
        return count

    @property
    def active_filter_labels(self) -> list[str]:
        """Short, buyer-readable summaries for the active-filter strip."""
        if not self.is_bound:
            return []
        self.is_valid()
        data = self.cleaned_data
        labels: list[str] = []
        if data.get("q"):
            labels.append(f"جست‌وجو: {data['q']}")
        if data.get("stone"):
            labels.append(f"نوع سنگ: {data['stone'].name}")
        if data.get("processing_type"):
            labels.append(f"فرآوری: {data['processing_type']}")
        labels.extend(f"کاربرد: {item.name}" for item in data.get("applications") or [])

        for name in ("availability", "sort", "state", "price_tier"):
            value = data.get(name)
            if not value or name not in self.fields:
                continue
            choices = dict(self.fields[name].choices)
            if name == "sort" and value == "recent":
                continue
            labels.append(f"{self.fields[name].label}: {choices.get(value, value)}")

        for name in ("price_min", "price_max", "min_qty_sqm"):
            if data.get(name) is not None:
                labels.append(f"{self.fields[name].label}: {data[name]:f}")
        if data.get("application_match") == "all":
            labels.append("تطبیق: همه کاربردها")
        return labels


class OwnerItemFilterForm(ItemFilterForm):
    price_tier = forms.ChoiceField(
        required=False,
        label="کانال قیمت",
        choices=(("b2c", "قیمت مشتری"), ("b2b", "قیمت همکار")),
        initial="b2c",
        widget=forms.Select(attrs=_TEXT),
    )
    state = forms.ChoiceField(
        required=False, label="وضعیت", choices=OWNER_STATE_CHOICES, widget=forms.Select(attrs=_TEXT)
    )

    def __init__(self, *args, can_view_prices=True, **kwargs):
        super().__init__(*args, **kwargs)
        if not can_view_prices:
            for name in ("price_tier", "price_min", "price_max"):
                self.fields.pop(name)
            self.fields["sort"].choices = [choice for choice in SORT_CHOICES if not choice[0].startswith("price_")]

    @property
    def state_value(self) -> str:
        self.is_valid()
        return getattr(self, "cleaned_data", {}).get("state", "")

    @property
    def advanced_scalar_fields(self):
        names = [
            "state",
            "stone",
            "processing_type",
            "availability",
            "price_tier",
            "price_min",
            "price_max",
            "min_qty_sqm",
            "sort",
            "application_match",
        ]
        return [self[name] for name in names if name in self.fields]


class MarketplaceItemFilterForm(ItemFilterForm):
    """Buyer filters without controls that cannot change marketplace results."""

    @property
    def advanced_scalar_fields(self):
        names = [
            "stone",
            "processing_type",
            "price_min",
            "price_max",
            "min_qty_sqm",
            "sort",
            "application_match",
        ]
        return [self[name] for name in names]
