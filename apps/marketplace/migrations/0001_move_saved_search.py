import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Takes SavedSearch over from the partners app without touching its rows:
    the model is re-created in state only (still pointing at the existing
    ``partners_savedsearch`` table), then the table is renamed to
    ``marketplace_savedsearch``.
    """

    initial = True

    dependencies = [
        ("businesses", "0001_initial"),
        ("partners", "0002_remove_partner_models"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="SavedSearch",
                    fields=[
                        (
                            "id",
                            models.UUIDField(
                                default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                            ),
                        ),
                        ("name", models.CharField(max_length=150)),
                        ("query", models.JSONField(blank=True, default=dict)),
                        ("notify_enabled", models.BooleanField(default=True)),
                        ("last_matched_at", models.DateTimeField(blank=True, null=True)),
                        ("last_notified_at", models.DateTimeField(blank=True, null=True)),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        ("updated_at", models.DateTimeField(auto_now=True)),
                        (
                            "business",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="saved_searches",
                                to="businesses.business",
                            ),
                        ),
                        (
                            "user",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="saved_searches",
                                to=settings.AUTH_USER_MODEL,
                            ),
                        ),
                    ],
                    options={
                        "verbose_name": "جستجوی ذخیره\u200cشده",
                        "verbose_name_plural": "جستجوهای ذخیره\u200cشده",
                        "ordering": ["-updated_at"],
                        "db_table": "partners_savedsearch",
                    },
                ),
            ],
            database_operations=[],
        ),
        migrations.AlterModelTable(name="savedsearch", table=None),
    ]
