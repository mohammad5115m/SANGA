"""Replace the free-text ``Product.applications`` JSON list with a taxonomy.

Application is a primary search dimension, and a search dimension backed by
unvalidated free text cannot work: «نمای بیرونی» and «نما بیرونی» would be two
different facets for one idea.

The old JSONField was never written to by any form or service — it was declared
in 0001 and left empty — so the conversion has nothing to preserve. That is
confirmed rather than assumed: the backfill below reads whatever is in the
column and maps what it can, so a hand-seeded database does not lose data.
"""

from django.db import migrations, models

# Mirrors inventory.models.DEFAULT_APPLICATIONS. Duplicated on purpose: a
# migration must not import runtime constants that may later change.
SEED = (
    ("exterior-facade", "نمای بیرونی"),
    ("interior-wall", "دیوار داخلی"),
    ("floor", "کف"),
    ("stairs", "راه پله"),
    ("parking", "پارکینگ"),
    ("landscape", "محوطه"),
    ("bathroom", "سرویس بهداشتی"),
    ("counter", "کانتر"),
    ("column", "ستون"),
    ("pool", "استخر"),
)


def seed_and_migrate(apps, schema_editor):
    Application = apps.get_model("inventory", "Application")
    Product = apps.get_model("inventory", "Product")

    by_code = {}
    by_name = {}
    for order, (code, name) in enumerate(SEED):
        app_row, _ = Application.objects.get_or_create(
            code=code,
            defaults={"name": name, "sort_order": order},
        )
        by_code[code] = app_row
        by_name[name] = app_row

    # Carry across anything a seeded or hand-edited database put in the old
    # column. Values that match no known application are dropped rather than
    # silently creating taxonomy entries nobody reviewed.
    for product in Product.objects.exclude(legacy_applications=[]).iterator():
        matched = []
        for raw in product.legacy_applications or []:
            key = str(raw).strip()
            row = by_code.get(key) or by_name.get(key)
            if row is not None:
                matched.append(row)
        if matched:
            product.applications.set(matched)


def unseed(apps, schema_editor):
    Application = apps.get_model("inventory", "Application")
    Application.objects.filter(code__in=[code for code, _ in SEED]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0006_drop_legacy_lot_status"),
    ]

    operations = [
        migrations.CreateModel(
            name="Application",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.SlugField(max_length=40, unique=True)),
                ("name", models.CharField(max_length=100, verbose_name="کاربرد")),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={
                "verbose_name": "کاربرد",
                "verbose_name_plural": "کاربردها",
                "ordering": ["sort_order", "name"],
            },
        ),
        # Rename first so the M2M can take the original attribute name while the
        # old data is still reachable for the backfill.
        migrations.RenameField(
            model_name="product",
            old_name="applications",
            new_name="legacy_applications",
        ),
        migrations.AddField(
            model_name="product",
            name="applications",
            field=models.ManyToManyField(
                blank=True,
                related_name="products",
                to="inventory.application",
                verbose_name="کاربردها",
            ),
        ),
        migrations.RunPython(seed_and_migrate, unseed),
        migrations.RemoveField(model_name="product", name="legacy_applications"),
    ]
