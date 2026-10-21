"""Move from reusable products and warehouse lots to one simple product item."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


STONES = (
    ("تراورتن", ["تراورتون", "travertine"], "T"),
    ("مرمریت", ["marble tile"], "M"),
    ("گرانیت", ["granite"], "G"),
    ("کریستال", ["crystal"], "C"),
    ("مرمر", ["انیکس", "اونیکس", "onyx", "مرمر انیکس"], "O"),
    ("لایمستون", ["لایم استون", "لایم‌استون", "limestone"], "L"),
    ("ترامیت", ["tramite"], "TR"),
    ("چینی", ["سنگ چینی", "چینی ازنا"], "CH"),
)


def _clean(value):
    return " ".join((value or "").replace("ي", "ی").replace("ك", "ک").split())


def _suffix(commercial_name, old_stone, canonical_stone):
    name = _clean(commercial_name)
    if name == "سنگ":
        return ""
    if name.startswith("سنگ "):
        name = name[4:].strip()
    for prefix in (_clean(old_stone), canonical_stone):
        if name == prefix:
            return ""
        if prefix and name.startswith(prefix + " "):
            return name[len(prefix) :].strip()
    return name


def _unique_slug(Product, business_id, original, lot_id):
    marker = str(lot_id).replace("-", "")[:8]
    base = (original or "product")[:205]
    candidate = f"{base}-lot-{marker}"[:220]
    counter = 1
    while Product.objects.filter(business_id=business_id, slug=candidate).exists():
        counter += 1
        suffix = f"-{counter}"
        candidate = f"{base[:220-len(suffix)]}{suffix}"
    return candidate


def migrate_products_and_lots(apps, schema_editor):
    VocabularyTerm = apps.get_model("inventory", "VocabularyTerm")
    Product = apps.get_model("inventory", "Product")
    InventoryLot = apps.get_model("inventory", "InventoryLot")

    # Colour/processing vocabulary rows are no longer part of this controlled
    # model. They have no foreign keys and can be retired safely.
    VocabularyTerm.objects.exclude(kind="stone_type").delete()

    canonical = {}
    active_names = []
    for order, (name, aliases, prefix) in enumerate(STONES):
        term, _ = VocabularyTerm.objects.update_or_create(
            kind="stone_type",
            name=name,
            defaults={
                "aliases": aliases,
                "code_prefix": prefix,
                "sort_order": order,
                "is_active": True,
            },
        )
        active_names.append(name)
        canonical[_clean(name).casefold()] = term
        for alias in aliases:
            canonical[_clean(alias).casefold()] = term
    VocabularyTerm.objects.filter(kind="stone_type").exclude(name__in=active_names).update(
        code_prefix="", is_active=False
    )

    for product in Product.objects.order_by("created_at").iterator():
        old_stone = _clean(product.stone_type)
        stone = canonical.get(old_stone.casefold())
        if stone is None:
            legacy_name = old_stone or "نامشخص"
            stone, _ = VocabularyTerm.objects.get_or_create(
                kind="stone_type",
                name=legacy_name,
                defaults={"code_prefix": "", "is_active": False},
            )
        suffix = _suffix(product.commercial_name, old_stone, stone.name)
        product.stone_id = stone.id
        product.name_suffix = suffix
        product.commercial_name = " ".join(part for part in ("سنگ", stone.name, suffix) if part)
        product.save(update_fields=["stone", "name_suffix", "commercial_name"])

    # Product and inventory item are now one-to-one. Preserve every lot by
    # cloning the shared descriptive row for the second and later lots.
    for product in Product.objects.order_by("created_at").iterator():
        lots = list(InventoryLot.objects.filter(product_id=product.id).order_by("created_at", "id"))
        for lot in lots[1:]:
            clone = Product.objects.create(
                business_id=product.business_id,
                commercial_name=product.commercial_name,
                name_suffix=product.name_suffix,
                slug=_unique_slug(Product, product.business_id, product.slug, lot.id),
                stone_id=product.stone_id,
                pattern=product.pattern,
                vein_notes=product.vein_notes,
                interior_suitable=product.interior_suitable,
                exterior_suitable=product.exterior_suitable,
                technical_notes=product.technical_notes,
                description_public=product.description_public,
                description_professional=product.description_professional,
                alt_names=product.alt_names,
                is_active=product.is_active,
            )
            clone.applications.set(product.applications.all())
            lot.product_id = clone.id
            lot.save(update_fields=["product"])

    # Avoid temporary collisions with seller-entered legacy codes while moving
    # to globally unique, immutable codes.
    for lot in InventoryLot.objects.iterator():
        lot.lot_code = f"TMP-{str(lot.id)}"
        lot.save(update_fields=["lot_code"])

    used = set()
    for lot in InventoryLot.objects.select_related("product__stone").order_by("created_at", "id").iterator():
        prefix = (lot.product.stone.code_prefix or "S").upper()
        salt = 0
        while True:
            digest = hashlib.sha256(f"{lot.id}:{salt}".encode()).hexdigest()[:6].upper()
            code = f"{prefix}-{digest}"
            if code not in used:
                used.add(code)
                break
            salt += 1
        lot.lot_code = code
        if lot.stock_mode != "exact":
            lot.available_sqm = None
        if lot.available_sqm is None:
            lot.stock_confirmed_at = None
            lot.stock_expires_at = None
        elif lot.stock_confirmed_at is not None:
            lot.stock_expires_at = lot.stock_confirmed_at + timedelta(days=lot.stock_valid_for_days)
        lot.processing_type = _clean(lot.processing_type) or "ساب خورده"
        lot.save(
            update_fields=[
                "lot_code",
                "available_sqm",
                "stock_confirmed_at",
                "stock_expires_at",
                "processing_type",
            ]
        )


class Migration(migrations.Migration):
    # The data step updates InventoryLot before later operations replace its
    # constraints. PostgreSQL cannot ALTER that table while deferred trigger
    # events from those updates are pending in the same transaction.
    atomic = False

    dependencies = [
        ("inventory", "0011_normalize_catalog_text"),
        ("catalog", "0003_snapshot_selected_catalogs"),
    ]

    operations = [
        migrations.AddField(
            model_name="vocabularyterm",
            name="code_prefix",
            field=models.CharField(
                blank=True,
                default="",
                help_text="حروف لاتین بزرگ؛ برای نمونه T یا CH",
                max_length=3,
                verbose_name="پیشوند کد محصول",
            ),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="vocabularyterm",
            name="kind",
            field=models.CharField(choices=[("stone_type", "نوع سنگ")], max_length=20),
        ),
        migrations.AddField(
            model_name="product",
            name="name_suffix",
            field=models.CharField(blank=True, default="", max_length=160, verbose_name="نام تکمیلی"),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="product",
            name="stone",
            field=models.ForeignKey(
                blank=True,
                limit_choices_to={"kind": "stone_type"},
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="products",
                to="inventory.vocabularyterm",
                verbose_name="نوع سنگ",
            ),
        ),
        migrations.AlterField(
            model_name="product",
            name="commercial_name",
            field=models.CharField(editable=False, max_length=200, verbose_name="نام تجاری"),
        ),
        migrations.RunPython(migrate_products_and_lots, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="inventorylot",
            name="uniq_lot_code_per_business",
        ),
        migrations.RemoveConstraint(
            model_name="inventorylot",
            name="lot_original_sqm_nonnegative",
        ),
        migrations.RemoveConstraint(
            model_name="inventorylot",
            name="lot_available_sqm_nonnegative",
        ),
        migrations.RemoveField(model_name="product", name="stone_type"),
        migrations.RemoveField(model_name="product", name="quarry_region"),
        migrations.RemoveField(model_name="product", name="primary_color"),
        migrations.RemoveField(model_name="inventorylot", name="stock_mode"),
        migrations.RemoveField(model_name="inventorylot", name="warehouse"),
        migrations.RemoveField(model_name="inventorylot", name="original_sqm"),
        migrations.RemoveField(model_name="inventorylot", name="slab_count"),
        migrations.RemoveField(model_name="inventorylot", name="bundle_count"),
        migrations.RemoveField(model_name="inventorylot", name="grade"),
        migrations.RemoveField(model_name="inventorylot", name="location_province"),
        migrations.RemoveField(model_name="inventorylot", name="location_city"),
        migrations.RemoveField(model_name="inventorylot", name="location_address"),
        migrations.AlterField(
            model_name="product",
            name="stone",
            field=models.ForeignKey(
                limit_choices_to={"kind": "stone_type"},
                on_delete=django.db.models.deletion.PROTECT,
                related_name="products",
                to="inventory.vocabularyterm",
                verbose_name="نوع سنگ",
            ),
        ),
        migrations.AlterField(
            model_name="inventorylot",
            name="product",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="lot",
                to="inventory.product",
            ),
        ),
        migrations.AlterField(
            model_name="inventorylot",
            name="lot_code",
            field=models.CharField(
                editable=False,
                max_length=12,
                unique=True,
                verbose_name="کد محصول",
            ),
        ),
        migrations.AlterField(
            model_name="inventorylot",
            name="available_sqm",
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                default=None,
                max_digits=12,
                null=True,
                validators=[django.core.validators.MinValueValidator(Decimal("0"))],
            ),
        ),
        migrations.AlterField(
            model_name="inventorylot",
            name="processing_type",
            field=models.CharField(default="ساب خورده", max_length=100),
        ),
        migrations.AddConstraint(
            model_name="inventorylot",
            constraint=models.CheckConstraint(
                condition=models.Q(available_sqm__isnull=True) | models.Q(available_sqm__gte=0),
                name="lot_available_sqm_nonnegative",
            ),
        ),
        migrations.AddConstraint(
            model_name="vocabularyterm",
            constraint=models.UniqueConstraint(
                condition=models.Q(kind="stone_type") & ~models.Q(code_prefix=""),
                fields=("code_prefix",),
                name="uniq_stone_code_prefix",
            ),
        ),
    ]
