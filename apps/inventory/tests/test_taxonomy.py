from decimal import Decimal

import pytest

from apps.core.testing import make_business, make_item, owner_membership
from apps.inventory.filters import ItemFilterSpec
from apps.inventory.forms import ProductItemForm
from apps.inventory.models import VocabularyTerm
from apps.inventory.policy import eligible_items
from apps.inventory.services import create_product_item, update_product_item
from apps.pricing.services import ensure_default_tiers

pytestmark = pytest.mark.django_db

EXPECTED = {
    "تراورتن": "T",
    "مرمریت": "M",
    "گرانیت": "G",
    "کریستال": "C",
    "مرمر": "O",
    "لایمستون": "L",
    "ترامیت": "TR",
    "چینی": "CH",
}


@pytest.fixture
def seller(db):
    ensure_default_tiers()
    business = make_business(name="سنگ واژه", owner_phone="09251110001")
    return business, owner_membership(business)


def test_the_migration_seeds_exact_stones_and_prefixes():
    rows = VocabularyTerm.objects.filter(
        kind=VocabularyTerm.Kind.STONE_TYPE, is_active=True
    ).values_list("name", "code_prefix")
    assert dict(rows) == EXPECTED


def test_crystal_and_chinese_are_distinct_terms():
    crystal = VocabularyTerm.objects.get(name="کریستال")
    chinese = VocabularyTerm.objects.get(name="چینی")
    assert crystal.pk != chinese.pk
    assert crystal.code_prefix == "C"
    assert chinese.code_prefix == "CH"


def test_marble_types_are_distinct_terms():
    assert VocabularyTerm.objects.get(name="مرمر").pk != VocabularyTerm.objects.get(name="مرمریت").pk


def test_the_product_form_accepts_only_active_controlled_stones():
    inactive = VocabularyTerm.objects.create(
        kind=VocabularyTerm.Kind.STONE_TYPE, name="قدیمی", is_active=False
    )
    form = ProductItemForm()
    assert not form.fields["stone"].queryset.filter(pk=inactive.pk).exists()
    assert form.fields["stone"].queryset.count() == len(EXPECTED)


def test_create_normalizes_suffix_and_processing(seller):
    business, membership = seller
    stone = VocabularyTerm.objects.get(name="مرمریت")
    lot = create_product_item(
        business=business,
        membership=membership,
        product_fields={"stone": stone, "name_suffix": "  لاشتر   روشن "},
        item_fields={
            "processing_type": "  ساب   خورده ",
            "available_sqm": Decimal("100"),
        },
    )
    assert lot.product.commercial_name == "سنگ مرمریت لاشتر روشن"
    assert lot.processing_type == "ساب خورده"


def test_edit_rebuilds_name_when_stone_changes(seller):
    business, membership = seller
    lot = make_item(business)
    granite = VocabularyTerm.objects.get(name="گرانیت")
    update_product_item(
        lot=lot,
        membership=membership,
        product_fields={"stone": granite, "name_suffix": "نطنز"},
        item_fields={},
    )
    lot.product.refresh_from_db()
    assert lot.product.commercial_name == "سنگ گرانیت نطنز"


def test_stone_filter_uses_the_controlled_fk(seller):
    business, _membership = seller
    chosen = make_item(business, product=None)
    found = ItemFilterSpec(stone=str(chosen.product.stone_id)).apply(
        eligible_items(audience="public"), audience="public"
    )
    assert list(found) == [chosen]
