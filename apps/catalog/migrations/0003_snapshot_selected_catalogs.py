"""Freeze rule catalogs into explicit live inventory selections.

The resulting catalog still renders current stock, price, media and visibility;
only its membership stops being a second, stored filtering language.
"""

from decimal import Decimal, InvalidOperation

from django.db import migrations, models
from django.utils import timezone


def _decimal(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _rule_matches(InventoryLot, catalog, rules):
    qs = InventoryLot.objects.filter(
        business_id=catalog.business_id,
        status="active",
        is_visible=True,
        availability_status="available",
        deleted_at__isnull=True,
    )
    q = str(rules.get("q") or "").strip()
    if q:
        qs = qs.filter(
            models.Q(product__commercial_name__icontains=q)
            | models.Q(product__stone_type__icontains=q)
            | models.Q(product__primary_color__icontains=q)
            | models.Q(product__quarry_region__icontains=q)
            | models.Q(lot_code__icontains=q)
            | models.Q(processing_type__icontains=q)
            | models.Q(grade__icontains=q)
        )
    for key, lookup in (
        ("stone_type", "product__stone_type__icontains"),
        ("color", "product__primary_color__icontains"),
        ("quarry_region", "product__quarry_region__icontains"),
        ("processing_type", "processing_type__icontains"),
        ("grade", "grade__icontains"),
    ):
        value = str(rules.get(key) or "").strip()
        if value:
            qs = qs.filter(**{lookup: value})

    applications = rules.get("applications") or []
    if isinstance(applications, str):
        applications = [applications]
    if applications:
        qs = qs.filter(product__applications__code__in=applications)

    for key, lookup in (
        ("thickness_min", "thickness_mm__gte"),
        ("thickness_max", "thickness_mm__lte"),
        ("min_qty_sqm", "available_sqm__gte"),
    ):
        value = _decimal(rules.get(key))
        if value is not None:
            qs = qs.filter(**{lookup: value})

    stock_mode = str(rules.get("stock_mode") or "")
    if stock_mode in {"exact", "unlimited", "inquiry"}:
        qs = qs.filter(stock_mode=stock_mode)

    price_min = _decimal(rules.get("price_min"))
    price_max = _decimal(rules.get("price_max"))
    only_special = bool(rules.get("only_special"))
    if price_min is not None or price_max is not None or only_special:
        now = timezone.now()
        live_price = models.Case(
            models.When(
                prices__tier__code="b2c",
                prices__mode="fixed",
                prices__special_amount__isnull=False,
                prices__special_until__gt=now,
                then=models.F("prices__special_amount"),
            ),
            models.When(
                prices__tier__code="b2c",
                prices__mode="fixed",
                prices__price_expires_at__gt=now,
                then=models.F("prices__amount"),
            ),
            default=None,
            output_field=models.DecimalField(max_digits=14, decimal_places=2),
        )
        qs = qs.annotate(_catalog_price=live_price)
        if price_min is not None:
            qs = qs.filter(_catalog_price__gte=price_min)
        if price_max is not None:
            qs = qs.filter(_catalog_price__lte=price_max)
        if only_special:
            qs = qs.filter(
                prices__tier__code="b2c",
                prices__mode="fixed",
                prices__special_amount__isnull=False,
                prices__special_until__gt=now,
            )
    return qs.distinct()


def snapshot_membership(apps, schema_editor):
    CustomCatalog = apps.get_model("catalog", "CustomCatalog")
    CustomCatalogItem = apps.get_model("catalog", "CustomCatalogItem")
    InventoryLot = apps.get_model("inventory", "InventoryLot")

    for catalog in CustomCatalog.objects.iterator():
        existing = list(catalog.items.order_by("sort_order", "id"))
        included = [row.lot_id for row in existing if row.inclusion == "include"]
        excluded = {row.lot_id for row in existing if row.inclusion == "exclude"}
        notes = {row.lot_id: row.note for row in existing if row.inclusion == "include"}

        selected = list(included)
        if catalog.mode in {"rule", "hybrid"}:
            matched = _rule_matches(InventoryLot, catalog, catalog.rules or {}).values_list(
                "pk", flat=True
            )
            selected.extend(matched)

        ordered = []
        seen = set()
        for lot_id in selected:
            if lot_id in excluded or lot_id in seen:
                continue
            seen.add(lot_id)
            ordered.append(lot_id)

        catalog.items.all().delete()
        CustomCatalogItem.objects.bulk_create(
            [
                CustomCatalogItem(
                    catalog_id=catalog.id,
                    lot_id=lot_id,
                    inclusion="include",
                    sort_order=position,
                    note=notes.get(lot_id, ""),
                )
                for position, lot_id in enumerate(ordered)
            ]
        )


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0002_customcatalog_mode_customcatalog_rules_and_more"),
        ("inventory", "0011_normalize_catalog_text"),
        ("pricing", "0004_simplify_price_semantics"),
    ]

    operations = [
        migrations.RunPython(snapshot_membership, migrations.RunPython.noop),
        migrations.RemoveField(model_name="customcatalogitem", name="inclusion"),
        migrations.RemoveField(model_name="customcatalog", name="mode"),
        migrations.RemoveField(model_name="customcatalog", name="rules"),
    ]
