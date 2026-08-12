from django.db import migrations


class Migration(migrations.Migration):
    """Drops the match-result table. Nothing references it, so no data migration
    is needed: matches were a derived cache of the scoring rule, never a record.
    """

    dependencies = [
        ("matching", "0002_initial"),
    ]

    operations = [
        migrations.DeleteModel(name="MatchResult"),
    ]
