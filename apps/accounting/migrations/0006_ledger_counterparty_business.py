"""Re-key the ledger from a hand-typed Contact onto the colleague's Business.

Additive first: the new columns arrive, 0007 backfills them, 0008 relaxes
``contact`` to nullable. No column is dropped here and none is dropped in the
next two either — a Contact that could not be mapped keeps its row, because
guessing a Business for it would corrupt a real balance.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("businesses", "0003_rewrite_capability_codes"),
        ("trading", "0001_initial"),
        ("accounting", "0005_ledgerentry_related_offer"),
    ]

    operations = [
        migrations.AddField(
            model_name="ledgerentry",
            name="counterparty_business",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="counterparty_ledger_entries",
                to="businesses.business",
                verbose_name="همکار",
            ),
        ),
        migrations.AddField(
            model_name="ledgerentry",
            name="legacy_counterparty_name",
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name="ledgerentry",
            name="related_trade",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="ledger_entries",
                to="trading.trade",
            ),
        ),
    ]
