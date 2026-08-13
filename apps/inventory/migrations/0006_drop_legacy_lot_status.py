"""Subtractive half: remove what 0005 has already copied elsewhere.

Runs strictly after the backfill, so no column is dropped before its data has
been carried onto the new lifecycle fields.

Gone from here:

* ``visibility``      — replaced by ``is_visible``
* seven ``status``    values that described availability, freshness or dead
                        reservation states rather than a workflow step
* ``offer_expires_at`` — never read anywhere, and actively confusing next to the
                        new stock/price validity windows
"""

from django.db import migrations, models

import apps.inventory.models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0005_backfill_item_lifecycle"),
    ]

    operations = [
        migrations.RemoveField(model_name="inventorylot", name="visibility"),
        migrations.RemoveField(model_name="inventorylot", name="offer_expires_at"),
        migrations.AlterField(
            model_name="inventorylot",
            name="status",
            field=models.CharField(
                choices=[("draft", "پیش‌نویس"), ("active", "فعال")],
                default="draft",
                max_length=32,
            ),
        ),
        # Uniqueness is safe now that 0005 has given every row its own token.
        migrations.AlterField(
            model_name="inventorylot",
            name="public_token",
            field=models.CharField(
                default=apps.inventory.models.generate_public_token,
                editable=False,
                max_length=32,
                unique=True,
            ),
        ),
        migrations.AlterField(
            model_name="inventorylot",
            name="lot_code",
            field=models.CharField(max_length=64, verbose_name="کد محصول"),
        ),
        migrations.AddIndex(
            model_name="inventorylot",
            index=models.Index(
                fields=["business", "stock_confirmed_at"],
                name="inventory_i_busines_stockc_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="inventorylot",
            index=models.Index(
                fields=["is_visible", "availability_status", "deleted_at"],
                name="inventory_i_elig_idx",
            ),
        ),
        migrations.AlterModelOptions(
            name="inventorylot",
            options={
                "ordering": ["-updated_at"],
                "verbose_name": "محصول قابل فروش",
                "verbose_name_plural": "محصولات قابل فروش",
            },
        ),
    ]
