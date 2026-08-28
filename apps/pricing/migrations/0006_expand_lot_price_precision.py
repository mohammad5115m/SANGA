from decimal import Decimal

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("pricing", "0005_lotprice_price_valid_days_range")]

    operations = [
        migrations.AlterField(
            model_name="lotprice",
            name="amount",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=16,
                null=True,
                validators=[django.core.validators.MinValueValidator(Decimal("0.01"))],
            ),
        ),
        migrations.AlterField(
            model_name="lotprice",
            name="special_amount",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=16,
                null=True,
                validators=[django.core.validators.MinValueValidator(Decimal("0.01"))],
                verbose_name="قیمت فروش ویژه",
            ),
        ),
    ]
