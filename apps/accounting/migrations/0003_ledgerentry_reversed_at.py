from django.db import migrations, models


def backfill_reversed_at(apps, schema_editor):
    """Stamp entries that were already reversed before this field existed.

    Without this, a trade reversed under the old rules would keep occupying the
    ``uniq_trade_entry_per_reservation`` slot forever. The stamp uses the
    reversal's own ``created_at`` so the history stays truthful.
    """
    LedgerEntry = apps.get_model('accounting', 'LedgerEntry')
    reversals = LedgerEntry.objects.filter(reverses__isnull=False).values_list(
        'reverses_id', 'created_at'
    )
    for original_id, created_at in reversals.iterator():
        LedgerEntry.objects.filter(pk=original_id, reversed_at__isnull=True).update(
            reversed_at=created_at
        )


class Migration(migrations.Migration):

    dependencies = [
        ('accounting', '0002_trade_entry_idempotency'),
    ]

    operations = [
        migrations.AddField(
            model_name='ledgerentry',
            name='reversed_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.RunPython(backfill_reversed_at, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name='ledgerentry',
            name='uniq_trade_entry_per_reservation',
        ),
        migrations.AddConstraint(
            model_name='ledgerentry',
            constraint=models.UniqueConstraint(condition=models.Q(('entry_type__in', ('sale', 'purchase')), ('related_reservation__isnull', False), ('reversed_at__isnull', True)), fields=('business', 'related_reservation'), name='uniq_trade_entry_per_reservation'),
        ),
    ]
