from __future__ import annotations

import secrets

from django.db import migrations, models

import apps.businesses.models


def populate_storefront_tokens(apps, schema_editor):
    Business = apps.get_model("businesses", "Business")
    for business in Business.objects.filter(storefront_token__isnull=True).iterator():
        while True:
            token = secrets.token_urlsafe(24)
            if not Business.objects.filter(storefront_token=token).exists():
                break
        business.storefront_token = token
        business.save(update_fields=["storefront_token"])


class Migration(migrations.Migration):
    dependencies = [("businesses", "0008_invoice_first_capabilities")]

    operations = [
        migrations.AddField(
            model_name="business",
            name="storefront_token",
            field=models.CharField(max_length=64, null=True, unique=True, verbose_name="توکن ویترین"),
        ),
        migrations.RunPython(populate_storefront_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="business",
            name="storefront_token",
            field=models.CharField(
                default=apps.businesses.models.generate_storefront_token,
                editable=False,
                max_length=64,
                unique=True,
                verbose_name="توکن ویترین",
            ),
        ),
    ]
