"""A product must be findable by the words a buyer actually types.

``normalize_persian_text`` existed and was applied to the incoming search query.
It was never applied to what was stored, so it protected one side of a comparison
and neither side of the problem: a product entered on an Arabic keyboard —
«مرمريت» with ي — was invisible to a search typed on a Persian one, and the
reverse. Both are ordinary; Iranian users have both keyboards.

Orthography is only half of it. «کریستال» and «چینی» are the same stone, and no
letter-level normalization will ever join them. That needs a list.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.core.testing import make_business, make_item, owner_membership
from apps.inventory.filters import ItemFilterSpec
from apps.inventory.models import Product, VocabularyTerm
from apps.inventory.policy import eligible_items
from apps.inventory.services import create_draft_item, create_or_get_product, update_item
from apps.inventory.taxonomy import canonical, clear_cache, vocabulary_context
from apps.pricing.services import ensure_default_tiers

ARABIC_YE = "مرمريت"  # ي
PERSIAN_YE = "مرمریت"  # ی

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def fresh_cache():
    clear_cache()
    yield
    clear_cache()


@pytest.fixture
def seller(db):
    ensure_default_tiers()
    business = make_business(name="سنگ واژه", owner_phone="09251110001")
    return business, owner_membership(business)


# --- the vocabulary is seeded -------------------------------------------------


def test_the_migration_seeds_every_dimension():
    for kind in VocabularyTerm.Kind:
        assert VocabularyTerm.objects.filter(kind=kind, is_active=True).exists(), kind


def test_the_form_suggestion_lists_cover_every_dimension():
    grouped = vocabulary_context()
    assert set(grouped) == {kind.value for kind in VocabularyTerm.Kind}
    assert "تراورتن" in grouped[VocabularyTerm.Kind.STONE_TYPE]


# --- orthography --------------------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        (ARABIC_YE, PERSIAN_YE),
        ("مرمریت  ", PERSIAN_YE),
        ("مرمریت\u00a0لاشتر", "مرمریت لاشتر"),
        ("  گرانیت   نهبندان ", "گرانیت نهبندان"),
    ],
)
def test_stored_text_is_normalized_on_the_way_in(seller, typed, expected):
    business, membership = seller
    product = create_or_get_product(
        business=business, membership=membership, commercial_name=typed
    )
    assert Product.objects.get(pk=product.pk).commercial_name == expected


def test_a_product_saved_with_an_arabic_keyboard_is_found_with_a_persian_one(seller):
    business, membership = seller
    product = create_or_get_product(
        business=business, membership=membership, commercial_name=f"{ARABIC_YE} لاشتر"
    )
    make_item(business, product=product, lot_code="VX-1", b2c="1000000")

    found = ItemFilterSpec.from_dict({"q": PERSIAN_YE}).apply(
        eligible_items(audience="public"), audience="public"
    )
    assert found.count() == 1


def test_a_product_saved_with_a_persian_keyboard_is_found_with_an_arabic_one(seller):
    """Both directions, because both keyboards are ordinary."""
    business, membership = seller
    product = create_or_get_product(
        business=business, membership=membership, commercial_name=f"{PERSIAN_YE} لاشتر"
    )
    make_item(business, product=product, lot_code="VX-2", b2c="1000000")

    found = ItemFilterSpec.from_dict({"q": ARABIC_YE}).apply(
        eligible_items(audience="public"), audience="public"
    )
    assert found.count() == 1


# --- the controlled vocabulary ------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "typed", "expected"),
    [
        (VocabularyTerm.Kind.STONE_TYPE, "چینی", "کریستال"),
        (VocabularyTerm.Kind.STONE_TYPE, "سنگ چینی", "کریستال"),
        (VocabularyTerm.Kind.STONE_TYPE, "تراورتون", "تراورتن"),
        (VocabularyTerm.Kind.STONE_TYPE, "marble", "مرمریت"),
        (VocabularyTerm.Kind.COLOR, "خاکستری", "طوسی"),
        (VocabularyTerm.Kind.COLOR, "سیاه", "مشکی"),
        (VocabularyTerm.Kind.FINISH, "پولیش", "صیقلی"),
        (VocabularyTerm.Kind.FINISH, "لدر", "چرمی"),
    ],
)
def test_a_known_synonym_is_stored_under_its_canonical_name(kind, typed, expected):
    assert canonical(kind, typed) == expected


def test_a_synonym_typed_with_the_other_keyboard_still_maps(seller):
    """Normalization runs before the lookup, so the two fixes compose rather than
    each covering only what the other misses."""
    assert canonical(VocabularyTerm.Kind.STONE_TYPE, "چيني") == "کریستال"


def test_two_sellers_using_different_words_become_one_filter_result(seller):
    business, membership = seller
    other = make_business(name="سنگ واژه دو", owner_phone="09251110002")

    make_item(
        business,
        product=create_or_get_product(
            business=business, membership=membership, commercial_name="سنگ الف", stone_type="چینی"
        ),
        lot_code="VX-3",
        b2c="1000000",
    )
    make_item(
        other,
        product=create_or_get_product(
            business=other,
            membership=owner_membership(other),
            commercial_name="سنگ ب",
            stone_type="کریستال",
        ),
        lot_code="VX-4",
        b2c="1000000",
    )

    found = ItemFilterSpec.from_dict({"stone_type": "کریستال"}).apply(
        eligible_items(audience="public"), audience="public"
    )
    assert found.count() == 2, "one stone, described two ways, must be one facet"


def test_an_unlisted_stone_is_kept_rather_than_refused(seller):
    """Iranian stone naming has a long tail. A seller who cannot record the stone
    they actually have stops recording stone."""
    business, membership = seller
    product = create_or_get_product(
        business=business,
        membership=membership,
        commercial_name="سنگ خاص",
        stone_type="یک سنگ محلی نادر",
    )
    assert product.stone_type == "یک سنگ محلی نادر"


# --- edits are normalized too -------------------------------------------------


def test_editing_an_item_normalizes_the_same_fields_as_creating_one(seller):
    """Otherwise an edit reintroduces exactly the unsearchable spelling the
    create path exists to prevent."""
    business, membership = seller
    product = create_or_get_product(business=business, membership=membership, commercial_name="سنگ ویرایش")
    lot = create_draft_item(business=business, membership=membership, product=product)

    update_item(
        lot=lot,
        membership=membership,
        fields={"processing_type": "پولیش", "grade": "  سوپر\u00a0یک  "},
    )

    lot.refresh_from_db()
    assert lot.processing_type == "صیقلی"
    assert lot.grade == "سوپر یک"


def test_creating_an_item_maps_its_finish_onto_a_term(seller):
    business, membership = seller
    product = create_or_get_product(business=business, membership=membership, commercial_name="سنگ ساخت")

    lot = create_draft_item(
        business=business, membership=membership, product=product, processing_type="لدر"
    )

    assert lot.processing_type == "چرمی"


# --- history is deliberately left alone ---------------------------------------


def test_a_sold_line_keeps_the_spelling_it_was_sold_under(seller):
    """Normalization is for the searchable catalog. A trade line and an invoice
    row are historical commercial facts, and a document that has already been
    handed to a customer does not get tidied up afterwards."""
    from decimal import Decimal

    from apps.invoicing.models import SalesInvoice
    from apps.trading.services import record_direct_sale

    business, membership = seller
    trade = record_direct_sale(
        seller_business=business,
        membership=membership,
        product_name=f"{ARABIC_YE} دست‌نویس",
        quantity_sqm=Decimal("10"),
        unit_price=Decimal("1000000"),
        customer_name="مشتری",
    )

    line = trade.items.get()
    assert ARABIC_YE in line.product_name, "a sold line must not be rewritten"
    invoice_line = SalesInvoice.objects.get(trade=trade).items.get()
    assert ARABIC_YE in invoice_line.product_name


# --- the seller-facing form offers the list -----------------------------------


def test_the_edit_form_carries_the_suggestion_lists(client, seller):
    business, membership = seller
    lot = make_item(business, lot_code="VX-9", b2c="1000000")
    client.force_login(membership.user)

    body = client.get(reverse("inventory:lot_edit", kwargs={"lot_id": lot.id})).content.decode()

    assert 'id="vocab-processing_type"' in body
    assert "صیقلی" in body
