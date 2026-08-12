from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Choices-only change: rows created before the partners app was removed keep
    their legacy `partner_request` / `partner_decision` kind. Notifications are
    a historical record, so nothing is rewritten or deleted.
    """

    dependencies = [
        ("notifications", "0002_alter_notification_kind"),
    ]

    operations = [
        migrations.AlterField(
            model_name="notification",
            name="kind",
            field=models.CharField(
                choices=[
                    ("saved_search_match", "تطابق جستجو"),
                    ("reservation_request", "درخواست رزرو"),
                    ("reservation_decision", "نتیجه رزرو"),
                    ("reservation_expired", "انقضای رزرو"),
                    ("general", "عمومی"),
                ],
                default="general",
                max_length=40,
            ),
        ),
    ]
