"""Split product notes by audience and retire the approved pattern field.

``RemoveField`` intentionally drops every stored ``Product.pattern`` value.
The product owner explicitly chose deletion rather than retaining historical
values, so this migration has no reverse data restoration path.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0013_inventorylot_creation_token_and_more"),
    ]

    operations = [
        migrations.RenameField(
            model_name="product",
            old_name="description_professional",
            new_name="description_colleague",
        ),
        migrations.AlterField(
            model_name="product",
            name="description_public",
            field=models.TextField(blank=True, verbose_name="توضیح برای مشتری"),
        ),
        migrations.AlterField(
            model_name="product",
            name="description_colleague",
            field=models.TextField(blank=True, verbose_name="توضیح برای همکار"),
        ),
        migrations.RenameField(
            model_name="inventorylot",
            old_name="defect_notes",
            new_name="description_private",
        ),
        migrations.AlterField(
            model_name="inventorylot",
            name="description_private",
            field=models.TextField(blank=True, verbose_name="توضیح شخصی"),
        ),
        migrations.AddField(
            model_name="inventorylot",
            name="private_address",
            field=models.TextField(blank=True, verbose_name="آدرس خصوصی محصول"),
        ),
        migrations.RemoveField(
            model_name="product",
            name="pattern",
        ),
    ]
