"""Every matching product must be reachable.

The discovery pages used to slice — 60 public cards, 80 marketplace cards, 100
owner rows — with no next page. A display cap is not pagination: past the cap the
products still matched the search and were simply unreachable, and the only way
to find one was to guess a narrower filter.

The other half of the rule is that a page link must not quietly reset the search.
Losing the filters on page two is the classic version of this bug, and it is
invisible unless a test follows the link.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.core.pagination import CARD_PAGE_SIZE
from apps.core.testing import make_business, make_item, make_product, owner_membership
from apps.pricing.services import ensure_default_tiers

ROWS = CARD_PAGE_SIZE + 5


@pytest.fixture
def stocked(db):
    """More products than fit on one page, half of one stone type."""
    ensure_default_tiers()
    seller = make_business(name="سنگ پرمحصول", owner_phone="09401110001")
    viewer = make_business(name="سنگ بیننده", owner_phone="09401110002")
    for index in range(ROWS):
        stone = "تراورتن" if index % 2 == 0 else "گرانیت"
        make_item(
            seller,
            product=make_product(
                seller,
                commercial_name=f"سنگ شماره {index}",
                stone_type=stone,
            ),
            lot_code=f"PG-{index:03d}",
            b2b="1000000",
            b2c="1500000",
        )
    return {"seller": seller, "viewer": viewer, "membership": owner_membership(seller)}


def _login(client, business) -> None:
    client.force_login(business.memberships.get(role="owner").user)
    session = client.session
    session["current_business_id"] = str(business.id)
    session.save()


def _cards(response) -> list:
    return response.context["cards"]


# --- everything is reachable ----------------------------------------------------


@pytest.mark.django_db
def test_public_search_offers_a_second_page(client, stocked):
    first = client.get(reverse("catalog:public_search"))
    assert len(_cards(first)) == CARD_PAGE_SIZE
    assert first.context["page"].has_next

    second = client.get(reverse("catalog:public_search"), {"page": 2})
    assert len(_cards(second)) == ROWS - CARD_PAGE_SIZE
    assert second.context["page"].total == ROWS


@pytest.mark.django_db
def test_no_product_is_reachable_on_two_pages_or_neither(client, stocked):
    seen = []
    for number in (1, 2):
        response = client.get(reverse("catalog:public_search"), {"page": number})
        seen.extend(card["lot"].lot_code for card in _cards(response))

    assert len(seen) == ROWS
    assert len(set(seen)) == ROWS


@pytest.mark.django_db
def test_the_marketplace_pages_too(client, stocked):
    _login(client, stocked["viewer"])
    response = client.get(reverse("marketplace:home"), {"page": 2})
    assert response.status_code == 200
    assert len(_cards(response)) == ROWS - CARD_PAGE_SIZE


@pytest.mark.django_db
def test_the_owner_inventory_pages_too(client, stocked):
    _login(client, stocked["seller"])
    response = client.get(reverse("inventory:lot_list"))
    assert response.context["page"].total == ROWS


# --- the search survives the page link -------------------------------------------


@pytest.mark.django_db
def test_a_filter_is_preserved_across_pages(client, stocked):
    filtered = {"stone_type": "تراورتن", "page": 2}
    response = client.get(reverse("catalog:public_search"), filtered)

    page = response.context["page"]
    assert page.total == ROWS // 2 + ROWS % 2
    assert "stone_type" in page.querystring
    assert "page" not in page.querystring, "the pager appends its own page number"


@pytest.mark.django_db
def test_a_sort_is_preserved_across_pages(client, stocked):
    response = client.get(reverse("catalog:public_search"), {"sort": "price_asc"})
    assert "sort=price_asc" in response.context["page"].querystring


@pytest.mark.django_db
def test_the_pager_links_carry_the_filters(client, stocked):
    # A filter that still leaves more than one page, or there is no link to check.
    body = client.get(reverse("catalog:public_search"), {"q": "سنگ"}).content.decode()
    assert "q=" in body
    assert "page=2" in body


# --- edges ------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_page_beyond_the_end_shows_the_last_one(client, stocked):
    """Narrowing a filter while on page 7 is the normal way to arrive here.
    Answering with a 404 punishes the user for refining their search."""
    response = client.get(reverse("catalog:public_search"), {"page": 99})
    assert response.status_code == 200
    assert response.context["page"].number == response.context["page"].num_pages


@pytest.mark.django_db
def test_a_nonsense_page_number_shows_the_first_one(client, stocked):
    response = client.get(reverse("catalog:public_search"), {"page": "٪٪"})
    assert response.status_code == 200
    assert response.context["page"].number == 1


@pytest.mark.django_db
def test_an_empty_result_set_has_one_page_and_no_pager(client, stocked):
    response = client.get(reverse("catalog:public_search"), {"q": "چیزی-که-وجود-ندارد"})
    page = response.context["page"]
    assert page.total == 0
    assert page.is_paginated is False
    assert "بعدی" not in response.content.decode()


@pytest.mark.django_db
def test_a_unicode_query_survives_the_page_link(client, stocked):
    response = client.get(reverse("catalog:public_search"), {"q": "سنگ", "page": 2})
    assert response.status_code == 200
    assert "q=" in response.context["page"].querystring


# --- catalogs page too ------------------------------------------------------------


@pytest.mark.django_db
def test_a_large_shared_catalog_is_paged_not_truncated(client, stocked):
    from apps.catalog.services import create_custom_catalog

    catalog = create_custom_catalog(
        business=stocked["seller"],
        membership=stocked["membership"],
        title="همه محصولات",
        lot_ids=list(stocked["seller"].lots.values_list("pk", flat=True)),
    )
    url = reverse("catalog:shared_catalog", kwargs={"share_token": catalog.share_token})

    first = client.get(url)
    assert first.context["page"].total == ROWS
    assert len(_cards(first)) == CARD_PAGE_SIZE

    second = client.get(url, {"page": 2})
    assert len(_cards(second)) == ROWS - CARD_PAGE_SIZE


@pytest.mark.django_db
def test_resolving_a_catalog_does_not_load_every_membership(stocked, django_assert_max_num_queries):
    """Catalog membership remains a database subquery rather than a Python set."""
    from apps.catalog.selectors import resolve_catalog
    from apps.catalog.services import create_custom_catalog

    catalog = create_custom_catalog(
        business=stocked["seller"],
        membership=stocked["membership"],
        title="همه",
        lot_ids=list(stocked["seller"].lots.values_list("pk", flat=True)),
    )

    resolved = resolve_catalog(catalog)
    with django_assert_max_num_queries(1):
        total = resolved.count()

    assert total == ROWS


@pytest.mark.django_db
def test_query_count_does_not_grow_with_the_number_of_products(client, stocked, django_assert_max_num_queries):
    """The point of paging: page one costs the same however much matched."""
    with django_assert_max_num_queries(12):
        client.get(reverse("catalog:public_search"))

    for index in range(ROWS, ROWS * 3):
        make_item(
            stocked["seller"],
            product=make_product(stocked["seller"], commercial_name=f"سنگ اضافه {index}"),
            lot_code=f"XG-{index:03d}",
            b2c="1500000",
        )

    with django_assert_max_num_queries(12):
        response = client.get(reverse("catalog:public_search"))
    assert response.context["page"].total == ROWS * 3
    assert len(_cards(response)) == CARD_PAGE_SIZE


@pytest.mark.django_db
def test_paging_does_not_change_what_the_filters_mean(client, stocked):
    """Freshness-aware filters keep working across pages."""
    from apps.core.testing import expire_stock

    for lot in stocked["seller"].lots.all()[:ROWS - 2]:
        expire_stock(lot)

    response = client.get(reverse("catalog:public_search"), {"min_qty_sqm": Decimal("10")})
    assert response.context["page"].total == 2
