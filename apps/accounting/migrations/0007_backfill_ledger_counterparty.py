"""Copy each entry's counterparty from its Contact onto the new columns.

Two things make this migration unusual, and both are deliberate.

**It can rewrite immutable rows.** ``LedgerEntry.save()`` raises on update and
``delete()`` raises unconditionally, but Django's historical models strip custom
methods, so a data migration is the *only* place this backfill can happen. An
equivalent management command would fail on the first save.

**It never recomputes a balance.** ``balance_after`` is a stored running total.
This migration re-points the counterparty FK and copies a name; it does not
touch ``amount``, ``balance_delta`` or ``balance_after``. Rederiving them would
be rewriting financial history to match an assumption.
"""

from django.db import migrations


def forwards(apps, schema_editor):
    LedgerEntry = apps.get_model("accounting", "LedgerEntry")

    mapped = 0
    unmapped = 0

    rows = LedgerEntry.objects.select_related("contact", "contact__linked_business").iterator()
    batch = []
    for entry in rows:
        contact = entry.contact
        if contact is None:
            continue

        # The name is copied in both branches: a Business can be renamed, and the
        # books should still say who the row was filed under at the time.
        entry.legacy_counterparty_name = contact.display_name or ""

        if contact.linked_business_id:
            entry.counterparty_business_id = contact.linked_business_id
            mapped += 1
        else:
            # No reliable Business behind this Contact. The row keeps its
            # contact FK and its name, stays queryable, and is never posted to
            # again. Inventing a counterparty would put somebody else's money on
            # a colleague's account.
            unmapped += 1

        batch.append(entry)
        if len(batch) >= 500:
            LedgerEntry.objects.bulk_update(batch, ["counterparty_business", "legacy_counterparty_name"])
            batch = []

    if batch:
        LedgerEntry.objects.bulk_update(batch, ["counterparty_business", "legacy_counterparty_name"])

    if unmapped:
        print(
            f"\n  accounting.0007: {mapped} ledger entries mapped to a Business, "
            f"{unmapped} kept under their legacy contact name (no linked_business). "
            f"See docs/accounting.md."
        )


def backwards(apps, schema_editor):
    """Clear the new columns. ``contact`` was never removed, so nothing is lost."""
    LedgerEntry = apps.get_model("accounting", "LedgerEntry")
    LedgerEntry.objects.update(counterparty_business=None, legacy_counterparty_name="")


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0006_ledger_counterparty_business"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
