"""Subscription fields on Business.

Deliberately three columns and no billing engine: the MVP needs to tell a
browse-only account from a selling one and cap how many people share it.

Existing businesses become ``seller`` with a seat limit sized to the members they
already have. Defaulting them to ``browse`` would silently strip selling from
every account that was working the day before, and defaulting the seat limit to 1
would do the same to every team.
"""

from django.db import migrations, models


def size_existing_plans(apps, schema_editor):
    Business = apps.get_model("businesses", "Business")
    BusinessMembership = apps.get_model("businesses", "BusinessMembership")

    for business in Business.objects.all().iterator():
        members = BusinessMembership.objects.filter(business=business, status="active").count()
        business.plan = "seller"
        business.seat_limit = max(members, 1)
        business.active_until = None
        business.save(update_fields=["plan", "seat_limit", "active_until"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("businesses", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="business",
            name="plan",
            field=models.CharField(
                choices=[("browse", "فقط مشاهده"), ("seller", "فروشنده")],
                default="seller",
                max_length=20,
                verbose_name="پلن",
            ),
        ),
        migrations.AddField(
            model_name="business",
            name="seat_limit",
            field=models.PositiveSmallIntegerField(default=1, verbose_name="تعداد کاربر مجاز"),
        ),
        migrations.AddField(
            model_name="business",
            name="active_until",
            field=models.DateField(blank=True, null=True, verbose_name="اعتبار تا"),
        ),
        migrations.RunPython(size_existing_plans, noop),
    ]
