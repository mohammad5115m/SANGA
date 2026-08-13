"""Make ``contact`` optional and add the Trade idempotency constraint.

Runs after the backfill, so by the time ``contact`` stops being required every
row that had one has already had its counterparty copied across.

``uniq_trade_entry_per_trade`` mirrors the existing offer constraint exactly.
It is what makes finalizing a sale twice impossible at the database level, not
merely unlikely.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0007_backfill_ledger_counterparty"),
    ]

    operations = [
        migrations.AlterField(
            model_name="ledgerentry",
            name="contact",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="ledger_entries",
                to="contacts.contact",
            ),
        ),
        migrations.AddConstraint(
            model_name="ledgerentry",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ("entry_type__in", ("sale", "purchase")),
                    ("related_trade__isnull", False),
                    ("reversed_at__isnull", True),
                ),
                fields=("business", "related_trade"),
                name="uniq_trade_entry_per_trade",
            ),
        ),
        # The contact-only index served the old lookup "this contact's entries
        # across the platform", which no query asks any more.
        migrations.RemoveIndex(
            model_name="ledgerentry",
            name="accounting__contact_8917db_idx",
        ),
        migrations.AddIndex(
            model_name="ledgerentry",
            index=models.Index(
                fields=["business", "counterparty_business", "created_at"],
                name="accounting__biz_cpty_idx",
            ),
        ),
    ]
