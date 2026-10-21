import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("businesses", "0009_business_storefront_token"),
        ("catalog", "0003_snapshot_selected_catalogs"),
        ("inventory", "0014_product_private_details"),
    ]

    operations = [
        migrations.CreateModel(
            name="StorefrontCollection",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=120, verbose_name="عنوان")),
                ("description", models.CharField(blank=True, max_length=240, verbose_name="توضیح کوتاه")),
                ("is_active", models.BooleanField(default=False, verbose_name="نمایش در ویترین")),
                ("sort_order", models.PositiveIntegerField(default=0)),
                ("suggestion_kind", models.CharField(blank=True, choices=[("", "بدون پیشنهاد خودکار"), ("economic", "قیمت‌های اقتصادی"), ("fresh", "تازه‌های ویترین"), ("exterior", "مناسب نمای بیرونی")], default="", max_length=20, verbose_name="پیشنهاد سیستمی")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("business", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="storefront_collections", to="businesses.business")),
            ],
            options={"verbose_name": "مجموعه ویترین", "verbose_name_plural": "مجموعه‌های ویترین", "ordering": ["sort_order", "created_at"]},
        ),
        migrations.CreateModel(
            name="StorefrontCollectionItem",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("sort_order", models.PositiveIntegerField(default=0)),
                ("collection", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="catalog.storefrontcollection")),
                ("lot", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="storefront_collection_items", to="inventory.inventorylot")),
            ],
            options={"verbose_name": "محصول مجموعه ویترین", "verbose_name_plural": "محصولات مجموعه ویترین", "ordering": ["sort_order", "id"]},
        ),
        migrations.AddConstraint(model_name="storefrontcollection", constraint=models.UniqueConstraint(fields=("business", "title"), name="uniq_storefront_collection_title_per_business")),
        migrations.AddIndex(model_name="storefrontcollection", index=models.Index(fields=["business", "is_active", "sort_order"], name="catalog_sto_busines_cfac30_idx")),
        migrations.AddConstraint(model_name="storefrontcollectionitem", constraint=models.UniqueConstraint(fields=("collection", "lot"), name="uniq_lot_per_storefront_collection")),
    ]
