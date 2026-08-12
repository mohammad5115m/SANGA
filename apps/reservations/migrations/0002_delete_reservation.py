from django.db import migrations


class Migration(migrations.Migration):
    """Drops the reservation table.

    Depends on accounting.0005, which removes ``LedgerEntry.related_reservation``:
    the ledger keeps its rows, only the pointer to this table is dropped first.
    """

    dependencies = [
        ("reservations", "0001_initial"),
        ("accounting", "0005_ledgerentry_related_offer"),
    ]

    operations = [
        migrations.DeleteModel(name="Reservation"),
    ]
