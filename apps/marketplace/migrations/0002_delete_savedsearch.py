"""Drop SavedSearch.

It was the last consumer of Celery beat. Its half-hourly job re-ran every stored
filter against recent inventory and sent notifications, which is a guess about
what a colleague wants rather than something they asked for. Removing it empties
``CELERY_BEAT_SCHEDULE``.

The stored ``query`` JSON is not migrated anywhere. It used the old marketplace
filter vocabulary, which no longer exists, and the rows described searches
nobody has necessarily run since.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("marketplace", "0001_move_saved_search"),
    ]

    operations = [
        migrations.DeleteModel(name="SavedSearch"),
    ]
