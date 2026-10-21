"""One Trade, one invoice — enforced by the database.

The service was idempotent by lookup, which cannot hold under concurrency: two
requests could both find no invoice for a trade and both create one, leaving the
same commercial event documented twice.

Adding the constraint would fail with an opaque IntegrityError on any database
that already has such a pair, so the check runs first and says which trades are
affected. Collapsing duplicates automatically is deliberately not attempted: two
invoices for one trade may both have been sent to a buyer, and choosing which one
survives is a commercial decision, not a migration's.
"""

from django.conf import settings
from django.db import migrations, models


def refuse_existing_duplicates(apps, schema_editor):
    SalesInvoice = apps.get_model("invoicing", "SalesInvoice")
    duplicates = (
        SalesInvoice.objects.filter(trade__isnull=False)
        .values_list("trade_id", flat=True)
        .annotate(count=models.Count("id"))
        .filter(count__gt=1)
        .order_by()
    )
    offenders = list(duplicates[:20])
    if offenders:
        raise RuntimeError(
            "Cannot enforce one invoice per trade: these trades already have more "
            f"than one invoice: {offenders}. Cancel the surplus documents first."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("businesses", "0003_rewrite_capability_codes"),
        ("invoicing", "0001_initial"),
        ("trading", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(refuse_existing_duplicates, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="salesinvoice",
            constraint=models.UniqueConstraint(
                condition=models.Q(("trade__isnull", False)),
                fields=("trade",),
                name="uniq_invoice_per_trade",
            ),
        ),
    ]
