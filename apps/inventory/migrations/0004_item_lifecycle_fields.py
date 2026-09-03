"""Additive half of the lifecycle rework.

Adds the four independent lifecycle axes alongside the old overloaded ``status``
and ``visibility`` columns. Nothing is dropped here and nothing is backfilled
here: 0005 copies the data across and 0006 removes what is then obsolete.

Two renames carry existing data forward untouched, because the old columns
already meant exactly what the new names say:

* ``inventory_confirmed_at`` -> ``stock_confirmed_at``
* ``archived_at``           -> ``deleted_at``
"""

import django.db.models.deletion
from django.db import migrations, models

import apps.inventory.models


class Migration(migrations.Migration):
    dependencies = [
        ("businesses", "0001_initial"),
        ("inventory", "0003_collapse_lot_visibility"),
    ]

    operations = [
        # Indexes reference the columns being renamed, so they have to go first
        # and come back in 0006 against the new names.
        migrations.RemoveIndex(
            model_name="inventorylot",
            name="inventory_i_busines_e24dce_idx",
        ),
        migrations.RemoveIndex(
            model_name="inventorylot",
            name="inventory_i_visibil_004429_idx",
        ),
        migrations.RenameField(
            model_name="inventorylot",
            old_name="inventory_confirmed_at",
            new_name="stock_confirmed_at",
        ),
        migrations.RenameField(
            model_name="inventorylot",
            old_name="archived_at",
            new_name="deleted_at",
        ),
        migrations.AlterField(
            model_name="inventorylot",
            name="deleted_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="inventorylot",
            name="is_visible",
            field=models.BooleanField(default=False, verbose_name="منتشر شده"),
        ),
        migrations.AddField(
            model_name="inventorylot",
            name="availability_status",
            field=models.CharField(
                choices=[("available", "موجود"), ("unavailable", "ناموجود")],
                default="available",
                max_length=20,
                verbose_name="وضعیت موجود بودن",
            ),
        ),
        migrations.AddField(
            model_name="inventorylot",
            name="stock_mode",
            field=models.CharField(
                choices=[
                    ("exact", "مقدار مشخص"),
                    ("unlimited", "موجودی نامحدود"),
                    ("inquiry", "استعلام موجودی"),
                ],
                default="exact",
                max_length=20,
                verbose_name="نوع موجودی",
            ),
        ),
        migrations.AddField(
            model_name="inventorylot",
            name="stock_valid_for_days",
            field=models.PositiveSmallIntegerField(default=7, verbose_name="اعتبار موجودی (روز)"),
        ),
        migrations.AddField(
            model_name="inventorylot",
            name="stock_expires_at",
            field=models.DateTimeField(blank=True, db_index=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="inventorylot",
            name="location_province",
            field=models.CharField(blank=True, max_length=100, verbose_name="استان"),
        ),
        migrations.AddField(
            model_name="inventorylot",
            name="location_city",
            field=models.CharField(blank=True, max_length=100, verbose_name="شهر"),
        ),
        migrations.AddField(
            model_name="inventorylot",
            name="location_address",
            field=models.TextField(blank=True, verbose_name="آدرس دقیق"),
        ),
        # Added without the unique constraint: every existing row would collide
        # on the single default. 0005 assigns real tokens, 0006 adds uniqueness.
        migrations.AddField(
            model_name="inventorylot",
            name="public_token",
            field=models.CharField(
                default=apps.inventory.models.generate_public_token,
                editable=False,
                max_length=32,
            ),
        ),
        # The Warehouse FK stops being required here so new items can be created
        # without one. The column itself survives until the data migration has
        # copied every address onto the item.
        migrations.AlterField(
            model_name="inventorylot",
            name="warehouse",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="lots",
                to="businesses.warehouse",
            ),
        ),
    ]
