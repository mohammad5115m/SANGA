from __future__ import annotations

import re
import uuid
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone

from apps.businesses.models import Business
from apps.core.forms import PersianNumericFormMixin
from apps.core.widgets import JalaliDateTimeWidget, JalaliDateWidget
from apps.inventory.models import InventoryLot

from .calculations import DISCOUNT_AMOUNT, to_display_amount, validate_display_unit
from .models import (
    BusinessInvoiceSettings,
    ChequeReceivable,
    LocalCounterparty,
    SalesInvoice,
    UserInvoiceSignature,
)
from .uploads import sanitize_invoice_image

_TEXT = {"class": "field-input"}
_MONEY = {**_TEXT, "inputmode": "decimal", "min": "0", "step": "0.01"}
UNIT_CHOICES = [
    ("متر مربع", "متر مربع"),
    ("عدد", "عدد"),
    ("متر", "متر"),
    ("تن", "تن"),
    ("کیلوگرم", "کیلوگرم"),
    ("پالت", "پالت"),
]
INVOICE_PALETTE_COLORS = {
    BusinessInvoiceSettings.Palette.FOREST: "#1f513c",
    BusinessInvoiceSettings.Palette.OCEAN: "#164e78",
    BusinessInvoiceSettings.Palette.CHARCOAL: "#30343b",
    BusinessInvoiceSettings.Palette.SAFFRON: "#7a4700",
}


class ManualInvoiceForm(PersianNumericFormMixin, forms.Form):
    CUSTOMER_FIELDS = ("customer_name", "customer_phone", "buyer_address", "paid_amount")
    BUSINESS_FIELDS = ("buyer_business",)
    LOCAL_FIELDS = ("local_counterparty", "local_name", "local_phone", "local_address")
    PARTNER_FIELDS = (
        "settlement_method",
        "cash_amount",
        "credit_amount",
        "cheque_amount",
        "cheque_reference",
        "cheque_bank",
        "cheque_due_date",
        "cheque_drawer",
    )
    CHEQUE_DETAIL_FIELDS = ("cheque_reference", "cheque_bank", "cheque_due_date", "cheque_drawer")
    numeric_fields = (
        "invoice_discount_value",
        "tax_amount",
        "shipping_amount",
        "adjustment_amount",
        "paid_amount",
        "cash_amount",
        "credit_amount",
        "cheque_amount",
    )
    submission_id = forms.UUIDField(required=False, widget=forms.HiddenInput)
    version = forms.IntegerField(required=False, min_value=1, widget=forms.HiddenInput)
    counterparty_mode = forms.ChoiceField(
        label="نوع خریدار",
        choices=(
            (SalesInvoice.Counterparty.CUSTOMER, "مشتری نهایی"),
            (SalesInvoice.Counterparty.BUSINESS, "همکار ثبت‌شده"),
            (SalesInvoice.Counterparty.LOCAL, "همکار محلی"),
        ),
        initial=SalesInvoice.Counterparty.CUSTOMER,
        required=False,
        widget=forms.RadioSelect,
    )
    buyer_business = forms.ModelChoiceField(
        label="کسب‌وکار خریدار",
        queryset=Business.objects.none(),
        required=False,
        widget=forms.Select(attrs=_TEXT),
    )
    local_counterparty = forms.ModelChoiceField(
        label="همکار محلی موجود",
        queryset=LocalCounterparty.objects.none(),
        required=False,
        widget=forms.Select(attrs=_TEXT),
    )
    local_name = forms.CharField(
        label="نام همکار محلی جدید",
        required=False,
        max_length=150,
        widget=forms.TextInput(attrs=_TEXT),
    )
    local_phone = forms.CharField(
        label="شماره تماس همکار محلی",
        required=False,
        max_length=20,
        widget=forms.TextInput(attrs={**_TEXT, "dir": "ltr", "inputmode": "tel"}),
    )
    local_address = forms.CharField(
        label="آدرس همکار محلی",
        required=False,
        widget=forms.Textarea(attrs={**_TEXT, "rows": 2}),
    )
    customer_name = forms.CharField(
        label="نام مشتری", required=False, max_length=150, widget=forms.TextInput(attrs=_TEXT)
    )
    customer_phone = forms.CharField(
        label="شماره تماس",
        required=False,
        max_length=20,
        widget=forms.TextInput(attrs={**_TEXT, "dir": "ltr", "inputmode": "tel"}),
    )
    buyer_address = forms.CharField(
        label="آدرس خریدار", required=False, widget=forms.Textarea(attrs={**_TEXT, "rows": 2})
    )
    issue_date = forms.DateField(
        label="تاریخ صدور",
        initial=timezone.localdate,
        widget=JalaliDateWidget(attrs={**_TEXT, "type": "date"}, format="%Y-%m-%d"),
    )
    currency = forms.ChoiceField(
        label="ارز مبنا",
        choices=SalesInvoice.Currency.choices,
        initial=SalesInvoice.Currency.IRR,
        widget=forms.Select(attrs=_TEXT),
    )
    display_unit = forms.ChoiceField(
        label="واحد نمایش",
        choices=SalesInvoice.DisplayUnit.choices,
        initial=SalesInvoice.DisplayUnit.IRR,
        widget=forms.Select(attrs=_TEXT),
    )
    invoice_discount_type = forms.ChoiceField(
        label="نوع تخفیف کلی",
        choices=SalesInvoice.DiscountType.choices,
        initial=SalesInvoice.DiscountType.NONE,
        widget=forms.Select(attrs=_TEXT),
    )
    invoice_discount_value = forms.DecimalField(
        label="مقدار تخفیف کلی",
        required=False,
        min_value=0,
        max_digits=16,
        decimal_places=2,
        initial=0,
        widget=forms.TextInput(attrs=_MONEY),
    )
    tax_amount = forms.DecimalField(
        label="مالیات",
        required=False,
        min_value=0,
        max_digits=16,
        decimal_places=2,
        initial=0,
        widget=forms.TextInput(attrs=_MONEY),
    )
    shipping_amount = forms.DecimalField(
        label="هزینه ارسال",
        required=False,
        min_value=0,
        max_digits=16,
        decimal_places=2,
        initial=0,
        widget=forms.TextInput(attrs=_MONEY),
    )
    adjustment_amount = forms.DecimalField(
        label="افزایش مبلغ (اختیاری)",
        required=False,
        min_value=0,
        max_digits=16,
        decimal_places=2,
        initial=0,
        widget=forms.TextInput(attrs=_MONEY),
    )
    paid_amount = forms.DecimalField(
        label="مبلغ کامل دریافت‌شده از مشتری",
        required=False,
        min_value=0,
        max_digits=16,
        decimal_places=2,
        initial=0,
        widget=forms.TextInput(attrs=_MONEY),
    )
    settlement_method = forms.ChoiceField(
        label="روش تسویه",
        choices=SalesInvoice.SettlementMethod.choices,
        initial=SalesInvoice.SettlementMethod.CREDIT,
        required=False,
        widget=forms.Select(attrs=_TEXT),
    )
    cash_amount = forms.DecimalField(
        label="نقد",
        required=False,
        min_value=0,
        max_digits=16,
        decimal_places=2,
        initial=0,
        widget=forms.TextInput(attrs=_MONEY),
    )
    credit_amount = forms.DecimalField(
        label="اعتبار",
        required=False,
        min_value=0,
        max_digits=16,
        decimal_places=2,
        initial=0,
        widget=forms.TextInput(attrs=_MONEY),
    )
    cheque_amount = forms.DecimalField(
        label="چک",
        required=False,
        min_value=0,
        max_digits=16,
        decimal_places=2,
        initial=0,
        widget=forms.TextInput(attrs=_MONEY),
    )
    cheque_reference = forms.CharField(
        label="شماره چک", required=False, max_length=100, widget=forms.TextInput(attrs=_TEXT)
    )
    cheque_bank = forms.CharField(label="بانک", required=False, max_length=120, widget=forms.TextInput(attrs=_TEXT))
    cheque_due_date = forms.DateField(
        label="سررسید چک",
        required=False,
        widget=JalaliDateWidget(attrs={**_TEXT, "type": "date"}, format="%Y-%m-%d"),
    )
    cheque_drawer = forms.CharField(
        label="صادرکننده چک", required=False, max_length=150, widget=forms.TextInput(attrs=_TEXT)
    )
    notes = forms.CharField(label="توضیحات", required=False, widget=forms.Textarea(attrs={**_TEXT, "rows": 3}))
    payment_terms = forms.CharField(
        label="شرایط پرداخت", required=False, widget=forms.Textarea(attrs={**_TEXT, "rows": 2})
    )
    buyer_signature = forms.ImageField(
        label="امضای خریدار",
        required=False,
        help_text="اختیاری؛ PNG، WebP یا JPEG تا ۵ مگابایت.",
    )
    remove_buyer_signature = forms.BooleanField(label="حذف امضای خریدار فعلی", required=False)
    palette = forms.ChoiceField(
        label="پالت رنگ",
        choices=BusinessInvoiceSettings.Palette.choices,
        initial=BusinessInvoiceSettings.Palette.FOREST,
        widget=forms.Select(attrs=_TEXT),
    )
    primary_color = forms.CharField(
        label="رنگ اصلی",
        max_length=7,
        initial="#1f513c",
        widget=forms.TextInput(attrs={**_TEXT, "type": "color"}),
    )
    header_style = forms.ChoiceField(
        label="سبک سربرگ",
        choices=BusinessInvoiceSettings.HeaderStyle.choices,
        initial=BusinessInvoiceSettings.HeaderStyle.MODERN,
        widget=forms.Select(attrs=_TEXT),
    )
    logo_size = forms.ChoiceField(
        label="اندازه لوگو",
        choices=BusinessInvoiceSettings.LogoSize.choices,
        initial=BusinessInvoiceSettings.LogoSize.MEDIUM,
        widget=forms.Select(attrs=_TEXT),
    )
    show_bank_information = forms.BooleanField(label="نمایش اطلاعات بانکی", required=False, initial=True)
    show_stamp = forms.BooleanField(label="نمایش مهر", required=False, initial=True)
    show_signature = forms.BooleanField(label="نمایش امضای فروشنده", required=False, initial=True)

    def __init__(self, *args, business=None, **kwargs):
        self.business = business
        # Remove controls that do not belong to the submitted buyer mode before
        # Django runs individual field validation.  This prevents an invalid or
        # stale hidden ID from blocking the active path even when JavaScript is
        # unavailable or a crafted request submits every section.
        if args and args[0] is not None:
            data = self._mode_scoped_data(args[0])
            args = (data, *args[1:])
        elif kwargs.get("data") is not None:
            kwargs["data"] = self._mode_scoped_data(kwargs["data"])
        super().__init__(*args, **kwargs)
        self.fields["customer_name"].widget.attrs["list"] = "invoice-customer-options"
        self.fields["local_counterparty"].empty_label = "ثبت همکار محلی جدید"
        self.fields["local_counterparty"].widget.attrs["aria-controls"] = "invoice-local-new-fields"
        self.fields["issue_date"].input_formats = ["%Y-%m-%d"]
        self.fields["cheque_due_date"].input_formats = ["%Y-%m-%d"]
        if business is not None:
            self.fields["buyer_business"].queryset = (
                Business.objects.filter(status=Business.Status.ACTIVE).exclude(pk=business.pk).order_by("name")
            )
            self.fields["local_counterparty"].queryset = LocalCounterparty.objects.filter(
                owner_business=business, status=LocalCounterparty.Status.ACTIVE
            ).order_by("name")
        if not self.is_bound and business is not None:
            try:
                settings_row = business.invoice_settings
            except BusinessInvoiceSettings.DoesNotExist:
                settings_row = None
            if settings_row:
                self.initial.setdefault("currency", settings_row.default_currency)
                self.initial.setdefault("display_unit", settings_row.default_display_unit)
                self.initial.setdefault("payment_terms", settings_row.payment_terms)
                self.initial.setdefault("palette", settings_row.palette)
                self.initial.setdefault("primary_color", settings_row.primary_color)
                self.initial.setdefault("header_style", settings_row.header_style)
                self.initial.setdefault("logo_size", settings_row.logo_size)
                self.initial.setdefault("show_bank_information", settings_row.show_bank_information)
                self.initial.setdefault("show_stamp", settings_row.show_stamp)
                self.initial.setdefault("show_signature", settings_row.show_signature)

    @classmethod
    def _mode_scoped_data(cls, source):
        data = source.copy()
        mode = data.get("counterparty_mode") or SalesInvoice.Counterparty.CUSTOMER
        if mode == SalesInvoice.Counterparty.CUSTOMER:
            inactive = cls.BUSINESS_FIELDS + cls.LOCAL_FIELDS + cls.PARTNER_FIELDS
        elif mode == SalesInvoice.Counterparty.BUSINESS:
            inactive = cls.CUSTOMER_FIELDS + cls.LOCAL_FIELDS
        elif mode == SalesInvoice.Counterparty.LOCAL:
            inactive = cls.CUSTOMER_FIELDS + cls.BUSINESS_FIELDS
            if data.get("local_counterparty"):
                inactive += ("local_name", "local_phone", "local_address")
        else:
            # Preserve all values so ChoiceField reports the unsupported mode.
            return data
        for name in inactive:
            data.pop(name, None)
        return data

    def clean_buyer_signature(self):
        upload = self.cleaned_data.get("buyer_signature")
        if not upload:
            return None
        return sanitize_invoice_image(upload, stem="buyer-signature")

    def clean(self):
        cleaned = super().clean()
        currency = cleaned.get("currency")
        display = cleaned.get("display_unit")
        if currency and display:
            try:
                validate_display_unit(currency, display)
            except ValueError as exc:
                self.add_error("display_unit", str(exc))
        if cleaned.get("invoice_discount_type") == SalesInvoice.DiscountType.PERCENT:
            value = cleaned.get("invoice_discount_value") or 0
            if value > 100:
                self.add_error("invoice_discount_value", "درصد تخفیف نمی‌تواند بیشتر از ۱۰۰ باشد.")
        color = cleaned.get("primary_color", "")
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            self.add_error("primary_color", "رنگ معتبر انتخاب کنید.")
        elif contrast_ratio(color, "#ffffff") < Decimal("4.5"):
            self.add_error("primary_color", "رنگ اصلی باید برای متن سفید کنتراست حداقل ۴٫۵ به ۱ داشته باشد.")
        mode = cleaned.get("counterparty_mode") or SalesInvoice.Counterparty.CUSTOMER
        cleaned["counterparty_mode"] = mode
        if mode == SalesInvoice.Counterparty.CUSTOMER:
            for name in self.BUSINESS_FIELDS + self.LOCAL_FIELDS + self.PARTNER_FIELDS:
                cleaned[name] = None
            if not (cleaned.get("customer_name") or "").strip():
                self.add_error("customer_name", "نام مشتری را وارد کنید.")
        elif mode == SalesInvoice.Counterparty.BUSINESS:
            for name in self.CUSTOMER_FIELDS + self.LOCAL_FIELDS:
                cleaned[name] = None
            if not cleaned.get("buyer_business"):
                self.add_error("buyer_business", "کسب‌وکار خریدار را انتخاب کنید.")
        elif mode == SalesInvoice.Counterparty.LOCAL:
            for name in self.CUSTOMER_FIELDS + self.BUSINESS_FIELDS:
                cleaned[name] = None
            if cleaned.get("local_counterparty"):
                for name in ("local_name", "local_phone", "local_address"):
                    cleaned[name] = None
            elif not (cleaned.get("local_name") or "").strip():
                self.add_error("local_name", "همکار محلی موجود را انتخاب کنید یا نام جدید را وارد کنید.")
        if mode != SalesInvoice.Counterparty.CUSTOMER:
            cleaned["settlement_method"] = (
                cleaned.get("settlement_method") or SalesInvoice.SettlementMethod.CREDIT
            )
            cheque = cleaned.get("cheque_amount") or 0
            if cheque and (not (cleaned.get("cheque_reference") or "").strip() or not cleaned.get("cheque_due_date")):
                self.add_error("cheque_reference", "برای مبلغ چک، شماره و تاریخ سررسید الزامی است.")
            if not cheque:
                for name in self.CHEQUE_DETAIL_FIELDS:
                    cleaned[name] = None
        return cleaned

    def appearance(self) -> dict:
        return {
            "palette": self.cleaned_data["palette"],
            "primary_color": self.cleaned_data["primary_color"],
            "header_style": self.cleaned_data["header_style"],
            "logo_size": self.cleaned_data["logo_size"],
            "show_bank_information": self.cleaned_data["show_bank_information"],
            "show_stamp": self.cleaned_data["show_stamp"],
            "show_signature": self.cleaned_data["show_signature"],
        }


class InvoiceLineForm(PersianNumericFormMixin, forms.Form):
    numeric_fields = ("quantity", "unit_price", "discount_value")
    item = forms.ModelChoiceField(
        label="محصول ثبت‌شده",
        queryset=InventoryLot.objects.none(),
        required=False,
        widget=forms.HiddenInput,
    )
    product_name = forms.CharField(
        label="نام محصول", required=False, max_length=200, widget=forms.TextInput(attrs=_TEXT)
    )
    stone_type = forms.CharField(label="نوع سنگ", required=False, max_length=100, widget=forms.TextInput(attrs=_TEXT))
    grade = forms.CharField(label="سورت", required=False, max_length=50, widget=forms.TextInput(attrs=_TEXT))
    description = forms.CharField(
        label="توضیح ردیف", required=False, max_length=255, widget=forms.TextInput(attrs=_TEXT)
    )
    quantity = forms.DecimalField(
        label="مقدار",
        required=False,
        min_value=Decimal("0.001"),
        decimal_places=3,
        max_digits=12,
        widget=forms.TextInput(attrs={**_TEXT, "inputmode": "decimal", "min": "0.001", "step": "0.001"}),
    )
    unit = forms.ChoiceField(label="واحد", choices=UNIT_CHOICES, widget=forms.Select(attrs=_TEXT))
    unit_price = forms.DecimalField(
        label="قیمت واحد",
        required=False,
        min_value=Decimal("0.01"),
        decimal_places=2,
        max_digits=14,
        widget=forms.TextInput(attrs=_MONEY),
    )
    discount_type = forms.ChoiceField(
        label="نوع تخفیف",
        choices=SalesInvoice.DiscountType.choices,
        initial=SalesInvoice.DiscountType.NONE,
        widget=forms.Select(attrs=_TEXT),
    )
    discount_value = forms.DecimalField(
        label="تخفیف",
        required=False,
        min_value=0,
        decimal_places=2,
        max_digits=16,
        initial=0,
        widget=forms.TextInput(attrs=_MONEY),
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
        filled = any(cleaned.get(key) not in (None, "") for key in ("item", "product_name", "quantity", "unit_price"))
        if not filled:
            return cleaned
        if not cleaned.get("item") and not (cleaned.get("product_name") or "").strip():
            self.add_error("product_name", "محصول را انتخاب کنید یا نام آن را وارد کنید.")
        if cleaned.get("quantity") in (None, ""):
            self.add_error("quantity", "مقدار را وارد کنید.")
        if cleaned.get("unit_price") in (None, ""):
            self.add_error("unit_price", "قیمت را وارد کنید.")
        if cleaned.get("discount_type") == SalesInvoice.DiscountType.PERCENT:
            if (cleaned.get("discount_value") or 0) > 100:
                self.add_error("discount_value", "درصد تخفیف نمی‌تواند بیشتر از ۱۰۰ باشد.")
        return cleaned


class BaseInvoiceLineFormSet(forms.BaseFormSet):
    ordering_widget = forms.HiddenInput

    def __init__(self, *args, business=None, **kwargs):
        self.business = business
        super().__init__(*args, **kwargs)

    def get_form_kwargs(self, index):
        return {**super().get_form_kwargs(index), "business": self.business}

    def __iter__(self):
        # Invalid bound forms must keep the user's visual order on a retry.
        # Empty extra rows stay at the end; field prefixes remain unchanged.
        def position(form):
            try:
                return int(form["ORDER"].value())
            except (TypeError, ValueError):
                return float("inf")
        return iter(sorted(self.forms, key=position))


InvoiceLineFormSet = forms.formset_factory(
    InvoiceLineForm,
    formset=BaseInvoiceLineFormSet,
    extra=1,
    can_delete=True,
    can_order=True,
    max_num=100,
    validate_max=True,
)


class BusinessInvoiceSettingsForm(forms.ModelForm):
    remove_logo = forms.BooleanField(label="حذف لوگوی فعلی", required=False)
    remove_stamp = forms.BooleanField(label="حذف مهر فعلی", required=False)
    remove_signature = forms.BooleanField(label="حذف امضای فعلی", required=False)

    class Meta:
        model = BusinessInvoiceSettings
        fields = [
            "legal_name",
            "tax_id",
            "bank_information",
            "payment_terms",
            "logo",
            "remove_logo",
            "stamp",
            "remove_stamp",
            "signature",
            "remove_signature",
            "palette",
            "primary_color",
            "header_style",
            "logo_size",
            "show_bank_information",
            "show_stamp",
            "show_signature",
            "default_currency",
            "default_display_unit",
        ]
        widgets = {
            "legal_name": forms.TextInput(attrs=_TEXT),
            "tax_id": forms.TextInput(attrs=_TEXT),
            "bank_information": forms.Textarea(attrs={**_TEXT, "rows": 3}),
            "payment_terms": forms.Textarea(attrs={**_TEXT, "rows": 2}),
            "logo": forms.FileInput(attrs={"accept": "image/png,image/webp,image/jpeg"}),
            "stamp": forms.FileInput(attrs={"accept": "image/png,image/webp,image/jpeg"}),
            "signature": forms.FileInput(attrs={"accept": "image/png,image/webp,image/jpeg"}),
            "palette": forms.Select(attrs=_TEXT),
            "primary_color": forms.TextInput(attrs={**_TEXT, "type": "color"}),
            "header_style": forms.Select(attrs=_TEXT),
            "logo_size": forms.Select(attrs=_TEXT),
            "show_bank_information": forms.CheckboxInput(attrs={"class": "field-checkbox"}),
            "show_stamp": forms.CheckboxInput(attrs={"class": "field-checkbox"}),
            "show_signature": forms.CheckboxInput(attrs={"class": "field-checkbox"}),
            "default_currency": forms.Select(attrs=_TEXT),
            "default_display_unit": forms.Select(attrs=_TEXT),
        }
        labels = {
            "logo": "لوگو",
            "stamp": "مهر",
            "signature": "امضای رسمی کسب‌وکار",
            "palette": "رنگ‌بندی",
            "primary_color": "رنگ اصلی",
            "header_style": "سبک سربرگ",
            "logo_size": "اندازه لوگو",
            "show_bank_information": "نمایش اطلاعات بانکی",
            "show_stamp": "نمایش مهر",
            "show_signature": "نمایش امضای فروشنده",
            "default_currency": "ارز پیش‌فرض",
            "default_display_unit": "واحد نمایش پیش‌فرض",
        }

    def clean_primary_color(self):
        color = self.cleaned_data["primary_color"]
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            raise forms.ValidationError("رنگ معتبر انتخاب کنید.")
        if contrast_ratio(color, "#ffffff") < Decimal("4.5"):
            raise forms.ValidationError("کنتراست رنگ با متن سفید باید حداقل ۴٫۵ به ۱ باشد.")
        return color.lower()

    def clean(self):
        cleaned = super().clean()
        preset_color = INVOICE_PALETTE_COLORS.get(cleaned.get("palette"))
        if preset_color:
            # A preset and its primary color are one setting.  Persisting the
            # canonical color keeps documents consistent even without JS.
            cleaned["primary_color"] = preset_color
        currency = cleaned.get("default_currency")
        display = cleaned.get("default_display_unit")
        if currency and display:
            try:
                validate_display_unit(currency, display)
            except ValueError as exc:
                self.add_error("default_display_unit", str(exc))
        return cleaned

    def _clean_image(self, field: str):
        upload = self.cleaned_data.get(field)
        if self.data.get(f"remove_{field}") and not getattr(upload, "content_type", None):
            return False
        if upload and (hasattr(upload, "temporary_file_path") or getattr(upload, "content_type", None)):
            return sanitize_invoice_image(upload, stem=field)
        return upload

    def clean_logo(self):
        return self._clean_image("logo")

    def clean_stamp(self):
        return self._clean_image("stamp")

    def clean_signature(self):
        return self._clean_image("signature")


class InvoiceTemplateNameForm(forms.Form):
    name = forms.CharField(label="نام قالب", max_length=120, widget=forms.TextInput(attrs=_TEXT))


class InvoiceCancelForm(forms.Form):
    reason = forms.CharField(
        label="علت ابطال یا اصلاح",
        max_length=250,
        widget=forms.Textarea(attrs={**_TEXT, "rows": 2}),
    )


class PartnerDecisionForm(forms.Form):
    reason = forms.CharField(
        label="علت رد",
        required=False,
        max_length=250,
        widget=forms.Textarea(attrs={**_TEXT, "rows": 2}),
    )


class OfflineApprovalForm(forms.Form):
    signer_name = forms.CharField(label="نام امضاکننده", max_length=150, widget=forms.TextInput(attrs=_TEXT))
    confirmed_at = forms.DateTimeField(
        label="زمان تأیید خارج از سنگا",
        widget=JalaliDateTimeWidget(attrs={**_TEXT, "type": "datetime-local"}),
    )
    signature = forms.ImageField(
        label="تصویر امضای همین فاکتور",
        help_text="PNG، WebP یا JPEG تا ۵ مگابایت.",
    )
    attested = forms.BooleanField(label="گواهی می‌کنم تأیید این فاکتور را خارج از سنگا دریافت کرده‌ام.")

    def clean_signature(self):
        return sanitize_invoice_image(self.cleaned_data["signature"], stem="offline-signature")


class PersonalSignatureForm(forms.ModelForm):
    remove_signature = forms.BooleanField(label="حذف امضای شخصی", required=False)

    class Meta:
        model = UserInvoiceSignature
        fields = ["image"]
        labels = {"image": "امضای شخصی"}
        widgets = {"image": forms.FileInput(attrs={"accept": "image/png,image/webp,image/jpeg"})}

    def clean_image(self):
        upload = self.cleaned_data.get("image")
        if self.data.get("remove_signature") and not getattr(upload, "content_type", None):
            return False
        if upload and getattr(upload, "content_type", None):
            return sanitize_invoice_image(upload, stem="personal-signature")
        return upload


class ChequeStatusForm(forms.Form):
    status = forms.ChoiceField(
        label="وضعیت جدید",
        choices=ChequeReceivable.Status.choices,
        widget=forms.Select(attrs=_TEXT),
    )
    reason = forms.CharField(
        label="توضیح",
        required=False,
        max_length=250,
        widget=forms.Textarea(attrs={**_TEXT, "rows": 2}),
    )


def new_submission_id() -> uuid.UUID:
    return uuid.uuid4()


def contrast_ratio(first: str, second: str) -> Decimal:
    def luminance(value: str) -> Decimal:
        channels = [int(value[index : index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4 for channel in channels]
        return Decimal(str(0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]))

    light, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (light + Decimal("0.05")) / (dark + Decimal("0.05"))


def invoice_initial(invoice: SalesInvoice) -> dict:
    def display(value):
        return to_display_amount(value, currency=invoice.currency, display_unit=invoice.display_unit)

    discount_value = invoice.invoice_discount_value
    if invoice.invoice_discount_type == DISCOUNT_AMOUNT:
        discount_value = display(discount_value)
    appearance = invoice.appearance_snapshot or {}
    return {
        "counterparty_mode": invoice.counterparty_type,
        "buyer_business": invoice.buyer_business_id,
        "local_counterparty": invoice.local_counterparty_id,
        "customer_name": invoice.customer_name,
        "customer_phone": invoice.customer_phone,
        "buyer_address": invoice.buyer_address,
        "issue_date": invoice.issue_date,
        "currency": invoice.currency,
        "display_unit": invoice.display_unit,
        "invoice_discount_type": invoice.invoice_discount_type,
        "invoice_discount_value": discount_value,
        "tax_amount": display(invoice.tax_amount),
        "shipping_amount": display(invoice.shipping_amount),
        "adjustment_amount": display(invoice.adjustment_amount),
        "paid_amount": display(invoice.paid_amount),
        "settlement_method": invoice.settlement_method,
        "cash_amount": display(invoice.cash_amount),
        "credit_amount": display(invoice.credit_amount),
        "cheque_amount": display(invoice.cheque_amount),
        "cheque_reference": (invoice.cheque_details or {}).get("reference_number", ""),
        "cheque_bank": (invoice.cheque_details or {}).get("bank", ""),
        "cheque_due_date": (invoice.cheque_details or {}).get("due_date") or None,
        "cheque_drawer": (invoice.cheque_details or {}).get("drawer_name", ""),
        "notes": invoice.notes,
        "payment_terms": invoice.payment_terms,
        "palette": appearance.get("palette", "forest"),
        "primary_color": appearance.get("primary_color", "#1f513c"),
        "header_style": appearance.get("header_style", "modern"),
        "logo_size": appearance.get("logo_size", "medium"),
        "show_bank_information": appearance.get("show_bank_information", True),
        "show_stamp": appearance.get("show_stamp", True),
        "show_signature": appearance.get("show_signature", True),
    }


def invoice_line_initials(invoice: SalesInvoice) -> list[dict]:
    result = []
    for line in invoice.items.all():
        discount = line.discount_value
        if line.discount_type == DISCOUNT_AMOUNT:
            discount = to_display_amount(discount, currency=invoice.currency, display_unit=invoice.display_unit)
        result.append(
            {
                "item": line.item_id,
                "product_name": line.product_name,
                "stone_type": line.stone_type,
                "grade": line.grade,
                "description": line.description,
                "quantity": line.quantity,
                "unit": line.unit,
                "unit_price": to_display_amount(
                    line.unit_price, currency=invoice.currency, display_unit=invoice.display_unit
                ),
                "discount_type": line.discount_type,
                "discount_value": discount,
                "ORDER": line.sort_order,
            }
        )
    return result
