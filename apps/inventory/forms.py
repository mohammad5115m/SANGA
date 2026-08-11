from __future__ import annotations

from django import forms

from apps.businesses.models import Warehouse

from .models import InventoryLot, Product


class ProductPickForm(forms.Form):
    product = forms.ModelChoiceField(
        label="محصول موجود",
        queryset=Product.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "field-input"}),
    )
    commercial_name = forms.CharField(
        label="یا نام محصول جدید",
        required=False,
        max_length=200,
        widget=forms.TextInput(attrs={"class": "field-input", "placeholder": "مثلاً تراورتن عباس‌آباد"}),
    )
    stone_type = forms.CharField(
        label="نوع سنگ",
        required=False,
        max_length=100,
        widget=forms.TextInput(attrs={"class": "field-input", "placeholder": "تراورتن"}),
    )
    primary_color = forms.CharField(
        label="رنگ غالب",
        required=False,
        max_length=100,
        widget=forms.TextInput(attrs={"class": "field-input", "placeholder": "کرم"}),
    )
    quarry_region = forms.CharField(
        label="معدن/منطقه",
        required=False,
        max_length=150,
        widget=forms.TextInput(attrs={"class": "field-input"}),
    )

    def __init__(self, *args, business=None, **kwargs):
        super().__init__(*args, **kwargs)
        if business is not None:
            self.fields["product"].queryset = Product.objects.filter(business=business, is_active=True)

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("product") and not (cleaned.get("commercial_name") or "").strip():
            raise forms.ValidationError("یک محصول انتخاب کنید یا نام محصول جدید را وارد کنید.")
        return cleaned


class LotDetailsForm(forms.Form):
    warehouse = forms.ModelChoiceField(
        label="انبار",
        queryset=Warehouse.objects.none(),
        widget=forms.Select(attrs={"class": "field-input"}),
    )
    lot_code = forms.CharField(
        label="کد محموله (اختیاری)",
        required=False,
        max_length=64,
        widget=forms.TextInput(attrs={"class": "field-input", "placeholder": "خالی بماند = خودکار"}),
    )
    grade = forms.CharField(
        label="سورت/درجه",
        required=False,
        max_length=50,
        widget=forms.TextInput(attrs={"class": "field-input"}),
    )
    processing_type = forms.CharField(
        label="نوع فرآوری",
        required=False,
        max_length=100,
        widget=forms.TextInput(attrs={"class": "field-input", "placeholder": "صیقلی / هوند"}),
    )
    description = forms.CharField(
        label="توضیح",
        required=False,
        widget=forms.Textarea(attrs={"class": "field-input", "rows": 3}),
    )

    def __init__(self, *args, business=None, **kwargs):
        super().__init__(*args, **kwargs)
        if business is not None:
            self.fields["warehouse"].queryset = Warehouse.objects.filter(business=business, is_active=True)


class LotQuantityForm(forms.Form):
    available_sqm = forms.DecimalField(
        label="متراژ موجود (m²)",
        min_value=0,
        decimal_places=3,
        max_digits=12,
        widget=forms.NumberInput(attrs={"class": "field-input", "step": "0.001", "inputmode": "decimal"}),
    )
    slab_count = forms.IntegerField(
        label="تعداد اسلب",
        required=False,
        min_value=0,
        widget=forms.NumberInput(attrs={"class": "field-input", "inputmode": "numeric"}),
    )
    length_cm = forms.DecimalField(
        label="طول (cm)",
        required=False,
        min_value=0,
        decimal_places=2,
        max_digits=8,
        widget=forms.NumberInput(attrs={"class": "field-input", "step": "0.01"}),
    )
    width_cm = forms.DecimalField(
        label="عرض (cm)",
        required=False,
        min_value=0,
        decimal_places=2,
        max_digits=8,
        widget=forms.NumberInput(attrs={"class": "field-input", "step": "0.01"}),
    )
    thickness_mm = forms.DecimalField(
        label="ضخامت (mm)",
        required=False,
        min_value=0,
        decimal_places=2,
        max_digits=8,
        widget=forms.NumberInput(attrs={"class": "field-input", "step": "0.01"}),
    )


class LotPricesForm(forms.Form):
    b2b_amount = forms.DecimalField(
        label="قیمت همکار (B2B)",
        min_value=0,
        decimal_places=0,
        max_digits=14,
        widget=forms.NumberInput(attrs={"class": "field-input", "inputmode": "numeric"}),
    )
    b2c_amount = forms.DecimalField(
        label="قیمت مشتری (B2C)",
        min_value=0,
        decimal_places=0,
        max_digits=14,
        widget=forms.NumberInput(attrs={"class": "field-input", "inputmode": "numeric"}),
    )
    currency = forms.CharField(
        label="ارز",
        initial="IRR",
        max_length=3,
        widget=forms.TextInput(attrs={"class": "field-input", "dir": "ltr"}),
    )


class LotVisibilityForm(forms.Form):
    visibility = forms.ChoiceField(
        label="نمایش",
        choices=InventoryLot.Visibility.choices,
        widget=forms.Select(attrs={"class": "field-input"}),
    )
    is_urgent_sale = forms.BooleanField(
        label="فروش فوری",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "field-checkbox"}),
    )
    is_featured = forms.BooleanField(
        label="ویژه",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "field-checkbox"}),
    )


class LotMediaForm(forms.Form):
    images = forms.FileField(
        label="عکس / ویدیو",
        required=False,
        widget=forms.ClearableFileInput(attrs={"class": "field-input", "accept": "image/*,video/*"}),
    )
    is_primary = forms.BooleanField(
        label="تصویر اصلی",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "field-checkbox"}),
    )


class LotEditForm(forms.ModelForm):
    class Meta:
        model = InventoryLot
        fields = (
            "warehouse",
            "grade",
            "processing_type",
            "available_sqm",
            "slab_count",
            "length_cm",
            "width_cm",
            "thickness_mm",
            "description",
            "defect_notes",
            "visibility",
            "status",
            "is_urgent_sale",
            "is_featured",
        )
        widgets = {
            "warehouse": forms.Select(attrs={"class": "field-input"}),
            "grade": forms.TextInput(attrs={"class": "field-input"}),
            "processing_type": forms.TextInput(attrs={"class": "field-input"}),
            "available_sqm": forms.NumberInput(attrs={"class": "field-input", "step": "0.001"}),
            "slab_count": forms.NumberInput(attrs={"class": "field-input"}),
            "length_cm": forms.NumberInput(attrs={"class": "field-input", "step": "0.01"}),
            "width_cm": forms.NumberInput(attrs={"class": "field-input", "step": "0.01"}),
            "thickness_mm": forms.NumberInput(attrs={"class": "field-input", "step": "0.01"}),
            "description": forms.Textarea(attrs={"class": "field-input", "rows": 3}),
            "defect_notes": forms.Textarea(attrs={"class": "field-input", "rows": 2}),
            "visibility": forms.Select(attrs={"class": "field-input"}),
            "status": forms.Select(attrs={"class": "field-input"}),
            "is_urgent_sale": forms.CheckboxInput(attrs={"class": "field-checkbox"}),
            "is_featured": forms.CheckboxInput(attrs={"class": "field-checkbox"}),
        }

    def __init__(self, *args, business=None, **kwargs):
        super().__init__(*args, **kwargs)
        if business is not None:
            self.fields["warehouse"].queryset = Warehouse.objects.filter(business=business, is_active=True)


class InventoryFilterForm(forms.Form):
    q = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "field-input", "placeholder": "جستجو نام، کد، رنگ..."}),
    )
    status = forms.ChoiceField(
        required=False,
        choices=[("", "همه وضعیت‌ها")] + list(InventoryLot.Status.choices),
        widget=forms.Select(attrs={"class": "field-input"}),
    )
    visibility = forms.ChoiceField(
        required=False,
        choices=[("", "همه نمایش‌ها")] + list(InventoryLot.Visibility.choices),
        widget=forms.Select(attrs={"class": "field-input"}),
    )
    freshness = forms.ChoiceField(
        required=False,
        choices=[
            ("", "همه"),
            ("needs_confirmation", "نیاز به تأیید"),
            ("urgent", "فروش فوری"),
            ("draft", "پیش‌نویس"),
        ],
        widget=forms.Select(attrs={"class": "field-input"}),
    )
