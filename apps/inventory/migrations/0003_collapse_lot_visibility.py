from django.db import migrations, models

# Old -> new visibility. Both partner values meant "approved partners only",
# which is now simply "every business with an account".
FORWARD_MAP = {
    "all_partners": "colleagues",
    "selected_partners": "colleagues",
    "customer_catalog": "public",
}
# Lossy on purpose: colleagues collapses two legacy values into one.
BACKWARD_MAP = {
    "colleagues": "all_partners",
}


def _remap(apps, mapping):
    InventoryLot = apps.get_model("inventory", "InventoryLot")
    for old, new in mapping.items():
        InventoryLot.objects.filter(visibility=old).update(visibility=new)


def collapse_visibility(apps, schema_editor):
    _remap(apps, FORWARD_MAP)


def expand_visibility(apps, schema_editor):
    _remap(apps, BACKWARD_MAP)


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0002_alter_inventorylot_visibility"),
    ]

    operations = [
        migrations.RunPython(collapse_visibility, expand_visibility),
        migrations.AlterField(
            model_name="inventorylot",
            name="visibility",
            field=models.CharField(
                choices=[("private", "داخلی"), ("colleagues", "همکاران"), ("public", "عمومی")],
                default="private",
                max_length=32,
            ),
        ),
    ]
