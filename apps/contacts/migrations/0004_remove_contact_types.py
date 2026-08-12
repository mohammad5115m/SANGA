from django.db import migrations


class Migration(migrations.Migration):
    """Drop the customer / supplier / trader flags.

    Only stone sellers and traders hold accounts, so every contact is a colleague
    and the relationship type carried no product meaning. Nothing reads the
    columns — no report, price rule or ledger entry keys off them — so the values
    are dropped rather than migrated anywhere.
    """

    dependencies = [
        ('contacts', '0003_alter_contact_linked_business'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='contact',
            name='is_customer',
        ),
        migrations.RemoveField(
            model_name='contact',
            name='is_supplier',
        ),
        migrations.RemoveField(
            model_name='contact',
            name='is_trader',
        ),
    ]
