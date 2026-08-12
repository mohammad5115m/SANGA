from django.db import migrations


class Migration(migrations.Migration):
    """
    Drops the partnership tables and hands SavedSearch over to the marketplace
    app. The SavedSearch row here is state-only: the physical table is renamed
    by ``marketplace.0001_move_saved_search``, which depends on this migration,
    so no data is copied or lost.
    """

    dependencies = [
        ("partners", "0001_initial"),
    ]

    operations = [
        migrations.DeleteModel(name="PartnerRelation"),
        migrations.DeleteModel(name="SupplierFollow"),
        migrations.SeparateDatabaseAndState(
            state_operations=[migrations.DeleteModel(name="SavedSearch")],
            database_operations=[],
        ),
    ]
