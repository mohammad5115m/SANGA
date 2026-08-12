from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Choices-only change, like 0003: notifications are a historical record, so rows
    created while reservations existed keep their legacy `reservation_*` kind and
    are neither rewritten nor deleted.
    """

    dependencies = [
        ("notifications", "0003_drop_partner_notification_kinds"),
    ]

    operations = [
        migrations.AlterField(
            model_name="notification",
            name="kind",
            field=models.CharField(
                choices=[("saved_search_match", "تطابق جستجو"), ("general", "عمومی")],
                default="general",
                max_length=40,
            ),
        ),
    ]
