import django.db.models.deletion
from django.db import migrations, models


def drop_reservation_links(apps, schema_editor):
    """Null every ``related_reservation`` before the column goes away.

    Ledger entries are immutable financial records and are never deleted: only
    the pointer to the (also removed) reservation is dropped. Amounts, deltas and
    running balances are untouched, so no balance changes.
    """
    LedgerEntry = apps.get_model("accounting", "LedgerEntry")
    LedgerEntry.objects.filter(related_reservation__isnull=False).update(related_reservation=None)


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0004_ledger_entry_type_labels"),
        ("purchase_requests", "0001_initial"),
    ]

    operations = [
        # The constraint references related_reservation, so it has to go first.
        migrations.RemoveConstraint(
            model_name="ledgerentry",
            name="uniq_trade_entry_per_reservation",
        ),
        migrations.RunPython(drop_reservation_links, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="ledgerentry",
            name="related_reservation",
        ),
        migrations.AddField(
            model_name="ledgerentry",
            name="related_offer",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="ledger_entries",
                to="purchase_requests.purchaseoffer",
            ),
        ),
        # Same shape as the reservation constraint it replaces: at most one live
        # trade entry per (business, accepted offer), and a reversal frees the slot.
        migrations.AddConstraint(
            model_name="ledgerentry",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ("entry_type__in", ("sale", "purchase")),
                    ("related_offer__isnull", False),
                    ("reversed_at__isnull", True),
                ),
                fields=("business", "related_offer"),
                name="uniq_trade_entry_per_offer",
            ),
        ),
    ]
