"""Immutable sales-invoice snapshots and tenant-owned document preferences."""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models


def invoice_asset_path(instance, filename: str) -> str:
    business_id = getattr(instance, "business_id", None) or instance.seller_business_id
    return f"invoice-assets/{business_id}/{uuid.uuid4().hex}.png"


class BusinessInvoiceSettings(models.Model):
    """Editable defaults. Issued documents copy these into JSON snapshots."""

    class Palette(models.TextChoices):
        FOREST = "forest", "سبز جنگلی"
        OCEAN = "ocean", "آبی اقیانوسی"
        CHARCOAL = "charcoal", "ذغالی"
        SAFFRON = "saffron", "زعفرانی"
        CUSTOM = "custom", "رنگ دلخواه"

    class HeaderStyle(models.TextChoices):
        CLASSIC = "classic", "کلاسیک"
        MODERN = "modern", "مدرن"
        MINIMAL = "minimal", "مینیمال"

    class LogoSize(models.TextChoices):
        SMALL = "small", "کوچک"
        MEDIUM = "medium", "متوسط"
        LARGE = "large", "بزرگ"

    business = models.OneToOneField(
        "businesses.Business", on_delete=models.CASCADE, related_name="invoice_settings"
    )
    legal_name = models.CharField("نام رسمی فروشنده", max_length=200, blank=True)
    tax_id = models.CharField("شناسه/کد اقتصادی", max_length=64, blank=True)
    bank_information = models.TextField("اطلاعات بانکی", blank=True)
    payment_terms = models.TextField("شرایط پرداخت پیش‌فرض", blank=True)
    logo = models.ImageField(upload_to=invoice_asset_path, blank=True, null=True)
    stamp = models.ImageField(upload_to=invoice_asset_path, blank=True, null=True)
    signature = models.ImageField(upload_to=invoice_asset_path, blank=True, null=True)
    palette = models.CharField(max_length=20, choices=Palette.choices, default=Palette.FOREST)
    primary_color = models.CharField(max_length=7, default="#1f513c")
    header_style = models.CharField(
        max_length=20, choices=HeaderStyle.choices, default=HeaderStyle.MODERN
    )
    logo_size = models.CharField(
        max_length=12, choices=LogoSize.choices, default=LogoSize.MEDIUM
    )
    show_bank_information = models.BooleanField(default=True)
    show_stamp = models.BooleanField(default=True)
    show_signature = models.BooleanField(default=True)
    default_currency = models.CharField(
        max_length=3,
        choices=[("IRR", "ریال ایران"), ("EUR", "یورو"), ("USD", "دلار آمریکا")],
        default="IRR",
    )
    default_display_unit = models.CharField(
        max_length=3,
        choices=[("IRR", "ریال"), ("IRT", "تومان"), ("EUR", "یورو"), ("USD", "دلار")],
        default="IRR",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "تنظیمات فاکتور"
        verbose_name_plural = "تنظیمات فاکتور"

    def __str__(self) -> str:
        return f"تنظیمات فاکتور {self.business}"


class SalesInvoice(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "پیش‌نویس"
        ISSUED = "issued", "صادر شده"
        CANCELLED = "cancelled", "باطل شده"

    class Counterparty(models.TextChoices):
        BUSINESS = "business", "همکار"
        CUSTOMER = "customer", "مشتری"

    class PaymentStatus(models.TextChoices):
        UNPAID = "unpaid", "پرداخت‌نشده"
        PARTIAL = "partial", "پرداخت ناقص"
        PAID = "paid", "پرداخت‌شده"

    class DiscountType(models.TextChoices):
        NONE = "none", "بدون تخفیف"
        AMOUNT = "amount", "مبلغ ثابت"
        PERCENT = "percent", "درصد"

    class Currency(models.TextChoices):
        IRR = "IRR", "ریال ایران"
        EUR = "EUR", "یورو"
        USD = "USD", "دلار آمریکا"

    class DisplayUnit(models.TextChoices):
        IRR = "IRR", "ریال"
        IRT = "IRT", "تومان"
        EUR = "EUR", "یورو"
        USD = "USD", "دلار"

    class BalanceState(models.TextChoices):
        DEBTOR = "debtor", "بدهکار"
        CREDITOR = "creditor", "بستانکار"
        SETTLED = "settled", "تسویه"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    seller_business = models.ForeignKey(
        "businesses.Business", on_delete=models.PROTECT, related_name="sales_invoices"
    )
    submission_id = models.UUIDField(null=True, blank=True, editable=False)
    version = models.PositiveIntegerField(default=1, editable=False)
    number = models.CharField("شماره فاکتور", max_length=32, blank=True, default="")
    counterparty_type = models.CharField(
        max_length=20, choices=Counterparty.choices, default=Counterparty.BUSINESS
    )
    buyer_business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="received_invoices",
    )
    customer_name = models.CharField("نام مشتری", max_length=150, blank=True)
    customer_phone = models.CharField("موبایل مشتری", max_length=20, blank=True)
    buyer_name = models.CharField("نام خریدار", max_length=200)
    buyer_phone = models.CharField("شماره تماس خریدار", max_length=20, blank=True)
    buyer_address = models.TextField("آدرس خریدار", blank=True)
    trade = models.ForeignKey(
        "trading.Trade",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices",
    )
    issue_date = models.DateField("تاریخ صدور")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    payment_status = models.CharField(
        max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.UNPAID
    )
    currency = models.CharField(max_length=3, choices=Currency.choices, default=Currency.IRR)
    display_unit = models.CharField(
        max_length=3, choices=DisplayUnit.choices, default=DisplayUnit.IRR
    )
    gross_subtotal = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0"))
    line_discount_total = models.DecimalField(
        max_digits=16, decimal_places=2, default=Decimal("0")
    )
    net_items_total = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0"))
    invoice_discount_type = models.CharField(
        max_length=12, choices=DiscountType.choices, default=DiscountType.NONE
    )
    invoice_discount_value = models.DecimalField(
        max_digits=16, decimal_places=2, default=Decimal("0")
    )
    invoice_discount_amount = models.DecimalField(
        max_digits=16, decimal_places=2, default=Decimal("0")
    )
    tax_amount = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0"))
    shipping_amount = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0"))
    adjustment_amount = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0"))
    total_amount = models.DecimalField(
        "جمع کل",
        max_digits=16,
        decimal_places=2,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    paid_amount = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0"))
    previous_balance_snapshot = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0")
    )
    previous_balance_state = models.CharField(
        max_length=12, choices=BalanceState.choices, default=BalanceState.SETTLED
    )
    previous_balance_currency = models.CharField(
        max_length=3, choices=Currency.choices, default=Currency.IRR
    )
    previous_balance_included = models.BooleanField(default=False)
    amount_due = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    notes = models.TextField("توضیحات", blank=True)
    payment_terms = models.TextField("شرایط پرداخت", blank=True)
    seller_snapshot = models.JSONField(default=dict, blank=True)
    appearance_snapshot = models.JSONField(default=dict, blank=True)
    buyer_signature = models.ImageField(upload_to=invoice_asset_path, blank=True, null=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices_created",
    )
    issued_at = models.DateTimeField(null=True, blank=True, editable=False)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices_issued",
        editable=False,
    )
    cancelled_at = models.DateTimeField(null=True, blank=True, editable=False)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices_cancelled",
        editable=False,
    )
    cancel_reason = models.CharField("علت ابطال", max_length=250, blank=True)
    replaces_invoice = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="replacement_invoice",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "فاکتور فروش"
        verbose_name_plural = "فاکتورهای فروش"
        ordering = ["-issue_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["seller_business", "number"],
                condition=~models.Q(number=""),
                name="uniq_invoice_number_per_seller",
            ),
            models.UniqueConstraint(
                fields=["trade"],
                condition=models.Q(trade__isnull=False),
                name="uniq_invoice_per_trade",
            ),
            models.UniqueConstraint(
                fields=["seller_business", "submission_id"],
                condition=models.Q(submission_id__isnull=False),
                name="uniq_invoice_submission_per_seller",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(counterparty_type="business", buyer_business__isnull=False)
                    | models.Q(counterparty_type="customer", buyer_business__isnull=True)
                ),
                name="invoice_counterparty_matches_type",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    gross_subtotal__gte=0,
                    line_discount_total__gte=0,
                    invoice_discount_amount__gte=0,
                    tax_amount__gte=0,
                    shipping_amount__gte=0,
                    adjustment_amount__gte=0,
                    total_amount__gte=0,
                    paid_amount__gte=0,
                    previous_balance_snapshot__gte=0,
                    amount_due__gte=0,
                ),
                name="invoice_amounts_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(paid_amount__lte=models.F("total_amount")),
                name="invoice_paid_not_above_total",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(previous_balance_included=False)
                    | models.Q(previous_balance_currency=models.F("currency"))
                ),
                name="invoice_included_balance_same_currency",
            ),
            models.CheckConstraint(
                condition=models.Q(status="draft") | ~models.Q(number=""),
                name="invoice_finalized_has_number",
            ),
            models.CheckConstraint(
                condition=models.Q(status="draft") | models.Q(issued_at__isnull=False),
                name="invoice_finalized_has_issued_at",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status="cancelled")
                    | (
                        models.Q(cancelled_at__isnull=False)
                        & ~models.Q(cancel_reason="")
                    )
                ),
                name="invoice_cancelled_has_audit",
            ),
        ]
        indexes = [
            models.Index(fields=["seller_business", "-issue_date"]),
            models.Index(fields=["buyer_business", "-issue_date"]),
            models.Index(
                fields=["seller_business", "status", "-created_at"],
                name="invoice_seller_status_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.number or 'پیش‌نویس'} — {self.buyer_name}"

    def save(self, *args, **kwargs):
        if self.status != self.Status.DRAFT and (
            not self.number or self.issued_at is None or not self.seller_snapshot
        ):
            raise ValidationError("سند نهایی باید شماره، زمان صدور و اطلاعات ثابت فروشنده داشته باشد.")
        if self.status == self.Status.CANCELLED and (
            self.cancelled_at is None or not self.cancel_reason.strip()
        ):
            raise ValidationError("برای ابطال سند، زمان و علت ابطال الزامی است.")
        if self.pk and not self._state.adding:
            original = SalesInvoice.objects.filter(pk=self.pk).first()
            if original and original.status != self.Status.DRAFT:
                allowed = (
                    original.status == self.Status.ISSUED
                    and self.status == self.Status.CANCELLED
                )
                if not allowed and self.status != original.status:
                    raise ValidationError("فاکتور صادرشده قابل ویرایش نیست.")
                immutable = [
                    "number",
                    "buyer_name",
                    "buyer_phone",
                    "buyer_address",
                    "issue_date",
                    "currency",
                    "display_unit",
                    "gross_subtotal",
                    "line_discount_total",
                    "net_items_total",
                    "invoice_discount_type",
                    "invoice_discount_value",
                    "invoice_discount_amount",
                    "tax_amount",
                    "shipping_amount",
                    "adjustment_amount",
                    "total_amount",
                    "paid_amount",
                    "previous_balance_snapshot",
                    "previous_balance_state",
                    "previous_balance_currency",
                    "previous_balance_included",
                    "amount_due",
                    "notes",
                    "payment_terms",
                    "seller_snapshot",
                    "appearance_snapshot",
                    "buyer_signature",
                    "issued_at",
                    "issued_by_id",
                    "replaces_invoice_id",
                ]
                if any(getattr(self, field) != getattr(original, field) for field in immutable):
                    raise ValidationError("محتوای فاکتور صادرشده تغییرناپذیر است.")
                cancellation_audit = (
                    "cancelled_at",
                    "cancelled_by_id",
                    "cancel_reason",
                )
                if original.status == self.Status.CANCELLED and any(
                    getattr(self, field) != getattr(original, field)
                    for field in cancellation_audit
                ):
                    raise ValidationError("تاریخچه ابطال فاکتور تغییرناپذیر است.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != self.Status.DRAFT:
            raise ValidationError("فاکتور صادرشده قابل حذف نیست؛ آن را باطل کنید.")
        return super().delete(*args, **kwargs)

    @property
    def is_editable(self) -> bool:
        return self.status == self.Status.DRAFT

    @property
    def display_number(self) -> str:
        return self.number or "پیش‌نویس"

    @property
    def display_total_amount(self) -> Decimal:
        from .calculations import to_display_amount

        return to_display_amount(
            self.total_amount,
            currency=self.currency,
            display_unit=self.display_unit,
        )


class SalesInvoiceItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey(SalesInvoice, on_delete=models.CASCADE, related_name="items")
    item = models.ForeignKey(
        "inventory.InventoryLot",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoice_items",
    )
    product_name = models.CharField("نام محصول", max_length=200)
    stone_type = models.CharField("نوع سنگ", max_length=100, blank=True)
    grade = models.CharField("سورت", max_length=50, blank=True)
    description = models.CharField("توضیح", max_length=255, blank=True)
    quantity = models.DecimalField(
        "مقدار",
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    unit = models.CharField("واحد", max_length=20, default="متر مربع")
    unit_price = models.DecimalField(
        "قیمت واحد",
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    gross_total = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0"))
    discount_type = models.CharField(
        max_length=12,
        choices=SalesInvoice.DiscountType.choices,
        default=SalesInvoice.DiscountType.NONE,
    )
    discount_value = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0"))
    discount_amount = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0"))
    line_total = models.DecimalField(
        "جمع",
        max_digits=16,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        verbose_name = "ردیف فاکتور"
        verbose_name_plural = "ردیف‌های فاکتور"
        ordering = ["sort_order", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0), name="invoice_item_quantity_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(unit_price__gt=0), name="invoice_item_price_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    gross_total__gte=0,
                    discount_value__gte=0,
                    discount_amount__gte=0,
                    line_total__gte=0,
                ),
                name="invoice_item_amounts_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(discount_amount__lte=models.F("gross_total")),
                name="invoice_item_discount_not_above_gross",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product_name} × {self.quantity}"

    def save(self, *args, **kwargs):
        if self.invoice_id and self.invoice.status != SalesInvoice.Status.DRAFT:
            raise ValidationError("ردیف فاکتور صادرشده قابل تغییر نیست.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.invoice.status != SalesInvoice.Status.DRAFT:
            raise ValidationError("ردیف فاکتور صادرشده قابل حذف نیست.")
        return super().delete(*args, **kwargs)


class InvoiceTemplate(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        "businesses.Business", on_delete=models.CASCADE, related_name="invoice_templates"
    )
    name = models.CharField("نام قالب", max_length=120)
    payload = models.JSONField(default=dict)
    schema_version = models.PositiveSmallIntegerField(default=1, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "name"], name="uniq_invoice_template_name"
            )
        ]

    def __str__(self) -> str:
        return self.name
