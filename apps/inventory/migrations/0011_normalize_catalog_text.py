"""Seed the discovery vocabulary and normalize what sellers have already typed.

Two things happen here, both aimed at the same defect: a product entered with an
Arabic ي was invisible to a search typed with a Persian ی, because normalization
was applied to the query and never to what was stored.

1. Seed the controlled terms for stone type, colour and surface finish.
2. Rewrite the existing catalog columns through the same normalization new
   products now get on save, so old products become findable by the same queries
   that find new ones.

**What is deliberately not touched:** ``Trade``, ``TradeItem`` and
``SalesInvoiceItem``. Those columns are historical commercial facts — the
description of what was sold, on a document that may already have been printed
and handed to a customer. They are not searched, so normalizing them buys
nothing, and rewriting the text on a past invoice to tidy up an orthographic
difference is exactly the kind of silent change to history this codebase is
built to avoid. The same applies to ``Inquiry`` line snapshots.
"""

from __future__ import annotations

import re

from django.db import migrations

# Duplicated rather than imported, as in 0007. A migration must keep describing
# the same change after the application code moves on.
SEED: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "stone_type": (
        ("تراورتن", ("تراورتون", "travertine")),
        ("مرمریت", ("مرمر", "marble")),
        ("گرانیت", ("granite",)),
        ("کریستال", ("چینی", "سنگ چینی", "crystal")),
        ("مرمر انیکس", ("انیکس", "اونیکس", "onyx")),
        ("لایم استون", ("لایم‌استون", "limestone")),
        ("تراونیکس", ("تراونیکس", "traonyx")),
        ("چینی ازنا", ()),
        ("دهبید", ()),
    ),
    "color": (
        ("کرم", ("کرمی",)),
        ("سفید", ()),
        ("بژ", ()),
        ("طوسی", ("خاکستری", "گری")),
        ("مشکی", ("سیاه",)),
        ("قهوه‌ای", ("قهوه ای",)),
        ("قرمز", ("سرخ",)),
        ("زرد", ()),
        ("سبز", ()),
        ("صورتی", ()),
        ("چندرنگ", ("چند رنگ", "ملتی")),
    ),
    "processing_type": (
        ("صیقلی", ("پولیش", "براق")),
        ("ساب خورده", ("ساب‌خورده", "هوند")),
        ("چرمی", ("لدر",)),
        ("چکشی", ("بوش همر",)),
        ("سندبلاست", ("سند بلاست", "تیشه‌ای")),
        ("برش خورده", ("برش‌خورده", "کات")),
        ("آنتیک", ()),
    ),
}

_ZWNJ = "\u200c"
_NBSP = "\u00a0"


def _normalize(value: str) -> str:
    """The same rules as ``apps.core.persian.normalize_persian_text``.

    Inlined for the same reason the seed data is: this migration must go on
    producing the result it was reviewed for, whatever the helper becomes later.
    """
    text = (value or "").strip()
    text = text.replace("ي", "ی").replace("ك", "ک")
    text = text.replace(_NBSP, " ")
    text = re.sub(rf"[{_ZWNJ}]+", _ZWNJ, text)
    return re.sub(r"\s+", " ", text)


def seed_and_normalize(apps, schema_editor):
    VocabularyTerm = apps.get_model("inventory", "VocabularyTerm")
    Product = apps.get_model("inventory", "Product")
    InventoryLot = apps.get_model("inventory", "InventoryLot")

    lookups: dict[str, dict[str, str]] = {}
    for kind, terms in SEED.items():
        table: dict[str, str] = {}
        for order, (name, aliases) in enumerate(terms):
            VocabularyTerm.objects.get_or_create(
                kind=kind,
                name=name,
                defaults={"aliases": list(aliases), "sort_order": order},
            )
            table[_normalize(name).casefold()] = name
            for alias in aliases:
                table[_normalize(alias).casefold()] = name
        lookups[kind] = table

    def canonical(kind: str, value: str) -> str:
        text = _normalize(value)
        if not text:
            return ""
        return lookups[kind].get(text.casefold(), text)

    for product in Product.objects.iterator():
        updated = {
            "commercial_name": _normalize(product.commercial_name),
            "stone_type": canonical("stone_type", product.stone_type),
            "primary_color": canonical("color", product.primary_color),
            "quarry_region": _normalize(product.quarry_region),
            "pattern": _normalize(product.pattern),
        }
        if any(getattr(product, key) != value for key, value in updated.items()):
            for key, value in updated.items():
                setattr(product, key, value)
            product.save(update_fields=list(updated))

    for lot in InventoryLot.objects.iterator():
        updated = {
            "grade": _normalize(lot.grade),
            "processing_type": canonical("processing_type", lot.processing_type),
            "location_province": _normalize(lot.location_province),
            "location_city": _normalize(lot.location_city),
        }
        if any(getattr(lot, key) != value for key, value in updated.items()):
            for key, value in updated.items():
                setattr(lot, key, value)
            lot.save(update_fields=list(updated))


def unseed(apps, schema_editor):
    """Drop the vocabulary.

    The normalization is deliberately **not** reversed. There is no record of
    which spelling each value had before, and inventing one would corrupt data
    rather than restore it — and nothing depends on the old spellings, because
    they were the reason search did not work.
    """
    apps.get_model("inventory", "VocabularyTerm").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("inventory", "0010_vocabulary_terms")]

    operations = [migrations.RunPython(seed_and_normalize, unseed)]
