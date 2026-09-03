"""Use a single per-square-metre price contract and harden valid values."""

from decimal import Decimal

import django.core.validators
from django.db import migrations, models
from django.utils import timezone


def clean_prices(apps, schema_editor):
    LotPrice = apps.get_model("pricing", "LotPrice")
    now = timezone.now()

    # Per-slab and inquiry-only rows have no honest per-square-metre numeric
    # representation under the new contract. Preserve the row as inquiry rather
    # than silently changing what its amount means.
    LotPrice.objects.exclude(unit="per_sqm").update(mode="inquiry")
    LotPrice.objects.filter(mode="fixed", amount__lte=0).update(mode="inquiry")
    LotPrice.objects.filter(mode="inquiry").update(
        amount=None,
        price_confirmed_at=None,
        price_expires_at=None,
        special_amount=None,
        special_until=None,
    )

    for price in LotPrice.objects.filter(mode="fixed").iterator():
        special_is_invalid = (
            (price.special_amount is None) != (price.special_until is None)
            or (
                price.special_amount is not None
                and (price.amount is None or price.special_amount >= price.amount)
            )
            or (price.special_until is not None and price.special_until <= now)
        )
        if special_is_invalid:
            price.special_amount = None
            price.special_until = None
            price.save(update_fields=["special_amount", "special_until"])


class Migration(migrations.Migration):
    dependencies = [("pricing", "0003_price_freshness_and_special_sale")]

    operations = [
        migrations.RunPython(clean_prices, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="lotprice",
            name="price_fixed_requires_amount",
        ),
        migrations.RemoveField(model_name="lotprice", name="unit"),
        migrations.AlterField(
            model_name="lotprice",
            name="amount",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=14,
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
                max_digits=14,
                null=True,
                validators=[django.core.validators.MinValueValidator(Decimal("0.01"))],
                verbose_name="قیمت فروش ویژه",
            ),
        ),
        migrations.AddConstraint(
            model_name="lotprice",
            constraint=models.CheckConstraint(
                condition=models.Q(mode="inquiry") | models.Q(amount__isnull=False),
                name="price_fixed_requires_amount",
            ),
        ),
        migrations.AddConstraint(
            model_name="lotprice",
            constraint=models.CheckConstraint(
                condition=models.Q(amount__isnull=True) | models.Q(amount__gt=0),
                name="price_amount_positive",
            ),
        ),
        migrations.AddConstraint(
            model_name="lotprice",
            constraint=models.CheckConstraint(
                condition=models.Q(special_amount__isnull=True) | models.Q(special_amount__gt=0),
                name="price_special_positive",
            ),
        ),
        migrations.AddConstraint(
            model_name="lotprice",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(special_amount__isnull=True, special_until__isnull=True)
                    | models.Q(special_amount__isnull=False, special_until__isnull=False)
                ),
                name="price_special_pair",
            ),
        ),
        migrations.AddConstraint(
            model_name="lotprice",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(special_amount__isnull=True)
                    | (
                        models.Q(mode="fixed", amount__isnull=False)
                        & models.Q(special_amount__lt=models.F("amount"))
                    )
                ),
                name="price_special_below_amount",
            ),
        ),
    ]
