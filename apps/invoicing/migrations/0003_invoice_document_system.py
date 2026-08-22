from decimal import Decimal
import uuid

import apps.invoicing.models
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill_document_snapshots(apps, schema_editor):
    Invoice = apps.get_model("invoicing", "SalesInvoice")
    Item = apps.get_model("invoicing", "SalesInvoiceItem")
    invalid_invoices = list(
        Invoice.objects.filter(total_amount__lt=0).values_list("id", flat=True)[:20]
    )
    invalid_items = list(
        Item.objects.filter(
            models.Q(quantity__lte=0)
            | models.Q(unit_price__lte=0)
            | models.Q(line_total__lt=0)
        ).values_list("id", flat=True)[:20]
    )
    if invalid_invoices or invalid_items:
        raise RuntimeError(
            "Cannot add invoice amount constraints until invalid historical rows are fixed. "
            f"Invoices: {invalid_invoices}; items: {invalid_items}."
        )
    for invoice in Invoice.objects.select_related("seller_business").iterator():
        business = invoice.seller_business
        total = invoice.total_amount or Decimal("0")
        Invoice.objects.filter(pk=invoice.pk).update(
            display_unit=invoice.currency,
            gross_subtotal=total,
            net_items_total=total,
            amount_due=total,
            previous_balance_currency=invoice.currency,
            seller_snapshot={
                "name": business.name,
                "phone": business.phone,
                "address": business.address,
                "tax_id": "",
                "bank_information": "",
            },
            appearance_snapshot={
                "palette": "forest",
                "primary_color": "#1f513c",
                "header_style": "modern",
                "logo_size": "medium",
                "show_bank_information": True,
                "show_stamp": True,
                "show_signature": True,
            },
        )
    for item in Item.objects.all().iterator():
        Item.objects.filter(pk=item.pk).update(gross_total=item.line_total)


def prepare_legacy_number_constraint(apps, schema_editor):
    """The old schema required every draft to have a unique non-blank number."""
    Invoice = apps.get_model("invoicing", "SalesInvoice")
    for invoice in Invoice.objects.filter(number="").only("id").iterator():
        rollback_number = str(invoice.id).replace("-", "")
        Invoice.objects.filter(pk=invoice.pk).update(number=rollback_number)


class Migration(migrations.Migration):
    dependencies = [
        ("invoicing", "0002_salesinvoice_uniq_invoice_per_trade"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="salesinvoice",
            name="uniq_invoice_number_per_seller",
        ),
        migrations.AlterField(
            model_name="salesinvoice",
            name="number",
            field=models.CharField(blank=True, default="", max_length=32, verbose_name="شماره فاکتور"),
        ),
        migrations.AddField(
            model_name="salesinvoice", name="adjustment_amount",
            field=models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=16),
        ),
        migrations.AddField(
            model_name="salesinvoice", name="amount_due",
            field=models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=18),
        ),
        migrations.AddField(
            model_name="salesinvoice", name="appearance_snapshot",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="salesinvoice", name="buyer_address",
            field=models.TextField(blank=True, verbose_name="آدرس خریدار"),
        ),
        migrations.AddField(
            model_name="salesinvoice", name="buyer_phone",
            field=models.CharField(blank=True, max_length=20, verbose_name="شماره تماس خریدار"),
        ),
        migrations.AddField(
            model_name="salesinvoice", name="buyer_signature",
            field=models.ImageField(blank=True, null=True, upload_to=apps.invoicing.models.invoice_asset_path),
        ),
        migrations.AddField(
            model_name="salesinvoice", name="display_unit",
            field=models.CharField(
                choices=[("IRR", "ریال"), ("IRT", "تومان"), ("EUR", "یورو"), ("USD", "دلار")],
                default="IRR", max_length=3,
            ),
        ),
        migrations.AddField(
            model_name="salesinvoice", name="gross_subtotal",
            field=models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=16),
        ),
        migrations.AddField(
            model_name="salesinvoice", name="invoice_discount_amount",
            field=models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=16),
        ),
        migrations.AddField(
            model_name="salesinvoice", name="invoice_discount_type",
            field=models.CharField(
                choices=[("none", "بدون تخفیف"), ("amount", "مبلغ ثابت"), ("percent", "درصد")],
                default="none", max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="salesinvoice", name="invoice_discount_value",
            field=models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=16),
        ),
        migrations.AddField(
            model_name="salesinvoice", name="line_discount_total",
            field=models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=16),
        ),
        migrations.AddField(
            model_name="salesinvoice", name="net_items_total",
            field=models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=16),
        ),
        migrations.AddField(
            model_name="salesinvoice", name="paid_amount",
            field=models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=16),
        ),
        migrations.AddField(
            model_name="salesinvoice", name="payment_status",
            field=models.CharField(
                choices=[("unpaid", "پرداخت‌نشده"), ("partial", "پرداخت ناقص"), ("paid", "پرداخت‌شده")],
                default="unpaid", max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="salesinvoice", name="payment_terms",
            field=models.TextField(blank=True, verbose_name="شرایط پرداخت"),
        ),
        migrations.AddField(
            model_name="salesinvoice", name="previous_balance_currency",
            field=models.CharField(
                choices=[("IRR", "ریال ایران"), ("EUR", "یورو"), ("USD", "دلار آمریکا")],
                default="IRR", max_length=3,
            ),
        ),
        migrations.AddField(
            model_name="salesinvoice", name="previous_balance_included",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="salesinvoice", name="previous_balance_snapshot",
            field=models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=18),
        ),
        migrations.AddField(
            model_name="salesinvoice", name="previous_balance_state",
            field=models.CharField(
                choices=[("debtor", "بدهکار"), ("creditor", "بستانکار"), ("settled", "تسویه")],
                default="settled", max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="salesinvoice", name="seller_snapshot",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="salesinvoice", name="shipping_amount",
            field=models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=16),
        ),
        migrations.AddField(
            model_name="salesinvoice", name="tax_amount",
            field=models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=16),
        ),
        migrations.AlterField(
            model_name="salesinvoice", name="currency",
            field=models.CharField(
                choices=[("IRR", "ریال ایران"), ("EUR", "یورو"), ("USD", "دلار آمریکا")],
                default="IRR", max_length=3,
            ),
        ),
        migrations.AddField(
            model_name="salesinvoiceitem", name="discount_amount",
            field=models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=16),
        ),
        migrations.AddField(
            model_name="salesinvoiceitem", name="discount_type",
            field=models.CharField(
                choices=[("none", "بدون تخفیف"), ("amount", "مبلغ ثابت"), ("percent", "درصد")],
                default="none", max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="salesinvoiceitem", name="discount_value",
            field=models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=16),
        ),
        migrations.AddField(
            model_name="salesinvoiceitem", name="gross_total",
            field=models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=16),
        ),
        migrations.CreateModel(
            name="BusinessInvoiceSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("legal_name", models.CharField(blank=True, max_length=200, verbose_name="نام رسمی فروشنده")),
                ("tax_id", models.CharField(blank=True, max_length=64, verbose_name="شناسه/کد اقتصادی")),
                ("bank_information", models.TextField(blank=True, verbose_name="اطلاعات بانکی")),
                ("payment_terms", models.TextField(blank=True, verbose_name="شرایط پرداخت پیش‌فرض")),
                ("logo", models.ImageField(blank=True, null=True, upload_to=apps.invoicing.models.invoice_asset_path)),
                ("stamp", models.ImageField(blank=True, null=True, upload_to=apps.invoicing.models.invoice_asset_path)),
                ("signature", models.ImageField(blank=True, null=True, upload_to=apps.invoicing.models.invoice_asset_path)),
                ("palette", models.CharField(choices=[("forest", "سبز جنگلی"), ("ocean", "آبی اقیانوسی"), ("charcoal", "ذغالی"), ("saffron", "زعفرانی"), ("custom", "رنگ دلخواه")], default="forest", max_length=20)),
                ("primary_color", models.CharField(default="#1f513c", max_length=7)),
                ("header_style", models.CharField(choices=[("classic", "کلاسیک"), ("modern", "مدرن"), ("minimal", "مینیمال")], default="modern", max_length=20)),
                ("logo_size", models.CharField(choices=[("small", "کوچک"), ("medium", "متوسط"), ("large", "بزرگ")], default="medium", max_length=12)),
                ("show_bank_information", models.BooleanField(default=True)),
                ("show_stamp", models.BooleanField(default=True)),
                ("show_signature", models.BooleanField(default=True)),
                ("default_currency", models.CharField(choices=[("IRR", "ریال ایران"), ("EUR", "یورو"), ("USD", "دلار آمریکا")], default="IRR", max_length=3)),
                ("default_display_unit", models.CharField(choices=[("IRR", "ریال"), ("IRT", "تومان"), ("EUR", "یورو"), ("USD", "دلار")], default="IRR", max_length=3)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("business", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="invoice_settings", to="businesses.business")),
            ],
            options={"verbose_name": "تنظیمات فاکتور", "verbose_name_plural": "تنظیمات فاکتور"},
        ),
        migrations.CreateModel(
            name="InvoiceTemplate",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=120, verbose_name="نام قالب")),
                ("payload", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("business", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="invoice_templates", to="businesses.business")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.RunPython(backfill_document_snapshots, prepare_legacy_number_constraint),
        migrations.AddConstraint(
            model_name="salesinvoice",
            constraint=models.UniqueConstraint(
                condition=~models.Q(number=""),
                fields=("seller_business", "number"),
                name="uniq_invoice_number_per_seller",
            ),
        ),
        migrations.AddConstraint(
            model_name="salesinvoice",
            constraint=models.CheckConstraint(
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
        ),
        migrations.AddConstraint(
            model_name="salesinvoice",
            constraint=models.CheckConstraint(
                condition=models.Q(paid_amount__lte=models.F("total_amount")),
                name="invoice_paid_not_above_total",
            ),
        ),
        migrations.AddConstraint(
            model_name="salesinvoice",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(previous_balance_included=False)
                    | models.Q(previous_balance_currency=models.F("currency"))
                ),
                name="invoice_included_balance_same_currency",
            ),
        ),
        migrations.AddConstraint(
            model_name="salesinvoiceitem",
            constraint=models.CheckConstraint(condition=models.Q(quantity__gt=0), name="invoice_item_quantity_positive"),
        ),
        migrations.AddConstraint(
            model_name="salesinvoiceitem",
            constraint=models.CheckConstraint(condition=models.Q(unit_price__gt=0), name="invoice_item_price_positive"),
        ),
        migrations.AddConstraint(
            model_name="salesinvoiceitem",
            constraint=models.CheckConstraint(condition=models.Q(gross_total__gte=0, discount_value__gte=0, discount_amount__gte=0, line_total__gte=0), name="invoice_item_amounts_nonnegative"),
        ),
        migrations.AddConstraint(
            model_name="salesinvoiceitem",
            constraint=models.CheckConstraint(condition=models.Q(discount_amount__lte=models.F("gross_total")), name="invoice_item_discount_not_above_gross"),
        ),
        migrations.AddConstraint(
            model_name="invoicetemplate",
            constraint=models.UniqueConstraint(fields=("business", "name"), name="uniq_invoice_template_name"),
        ),
        migrations.AddIndex(
            model_name="salesinvoice",
            index=models.Index(fields=["seller_business", "status", "-created_at"], name="invoice_seller_status_idx"),
        ),
    ]
