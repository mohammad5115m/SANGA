"""Per-tier price mode, validity window and special sale; drop ContactPrice.

Special-sale pricing deliberately lands on ``LotPrice`` rather than on the item.
A single special price on the item would be an unlabelled number sitting outside
the tier gate, and the first public template to render it would leak a B2B
figure. On the tier row it inherits the protection ``amount`` already has.

``ContactPrice`` goes away entirely. It was a third pricing axis on top of B2B
and B2C, keyed to a manually-created Contact — and Contacts are being replaced
by the Business directory, so the override had nothing stable to hang off.
"""

from datetime import timedelta
from decimal import Decimal

import django.core.validators
from django.db import migrations, models
from django.utils import timezone


def confirm_existing_prices(apps, schema_editor):
    """Treat every stored price as confirmed at migration time.

    Leaving ``price_confirmed_at`` null would make every existing price read as
    «استعلام قیمت» the moment this deploys, blanking numbers across the whole
    marketplace. Sellers set these deliberately; the honest default is to start
    their validity window now rather than to pretend they were never confirmed.
    """
    LotPrice = apps.get_model("pricing", "LotPrice")
    now = timezone.now()
    LotPrice.objects.filter(price_confirmed_at__isnull=True).update(
        price_confirmed_at=now,
        price_expires_at=now + timedelta(days=7),
    )


def mark_inquiry_prices(apps, schema_editor):
    """Carry the old ``unit='inquiry_only'`` convention onto the new mode field."""
    LotPrice = apps.get_model("pricing", "LotPrice")
    LotPrice.objects.filter(unit="inquiry_only").update(mode="inquiry", amount=None, price_expires_at=None)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("pricing", "0002_contactprice"),
    ]

    operations = [
        migrations.AddField(
            model_name="lotprice",
            name="mode",
            field=models.CharField(
                choices=[("fixed", "قیمت مشخص"), ("inquiry", "استعلام قیمت")],
                default="fixed",
                max_length=20,
                verbose_name="نوع قیمت",
            ),
        ),
        migrations.AddField(
            model_name="lotprice",
            name="price_confirmed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="lotprice",
            name="price_valid_for_days",
            field=models.PositiveSmallIntegerField(default=7, verbose_name="اعتبار قیمت (روز)"),
        ),
        migrations.AddField(
            model_name="lotprice",
            name="price_expires_at",
            field=models.DateTimeField(blank=True, db_index=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="lotprice",
            name="special_amount",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=14,
                null=True,
                validators=[django.core.validators.MinValueValidator(Decimal("0"))],
                verbose_name="قیمت فروش ویژه",
            ),
        ),
        migrations.AddField(
            model_name="lotprice",
            name="special_until",
            field=models.DateTimeField(blank=True, null=True, verbose_name="پایان فروش ویژه"),
        ),
        # Nullable so inquiry-mode rows can stop carrying a meaningless zero.
        migrations.AlterField(
            model_name="lotprice",
            name="amount",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=14,
                null=True,
                validators=[django.core.validators.MinValueValidator(Decimal("0"))],
            ),
        ),
        migrations.RunPython(confirm_existing_prices, noop),
        migrations.RunPython(mark_inquiry_prices, noop),
        migrations.AddConstraint(
            model_name="lotprice",
            constraint=models.CheckConstraint(
                condition=models.Q(mode="inquiry") | models.Q(amount__isnull=False),
                name="price_fixed_requires_amount",
            ),
        ),
        migrations.AlterModelOptions(
            name="lotprice",
            options={"verbose_name": "قیمت محصول", "verbose_name_plural": "قیمت محصولات"},
        ),
        migrations.DeleteModel(name="ContactPrice"),
    ]
