"""Query budgets for the list pages.

These are not micro-benchmarks. Each budget is chosen to be **flat in the number
of rows**, so the test fails the moment a template or selector starts issuing one
query per item. That is the only performance bug this codebase is realistically
going to hit, and it is invisible in review.

When one of these fails, the fix is almost always a missing ``select_related`` /
``prefetch_related``, or a ``.count()`` in a template that should be
``|length`` — ``.count()`` ignores the prefetch cache.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.core.testing import make_business, make_item, make_product, owner_membership
from apps.inquiries.services import create_inquiry
from apps.invoicing.services import create_manual_invoice
from apps.pricing.services import ensure_default_tiers
from apps.trading.services import create_purchase_request, record_direct_sale

ROWS = 8


@pytest.fixture
def busy(db):
    """One business with several of everything, so an N+1 is visible."""
    ensure_default_tiers()
    seller = make_business(name="سنگ شلوغ", owner_phone="09251110001")
    buyer = make_business(name="سنگ خریدار", owner_phone="09251110002")
    seller_m = owner_membership(seller)
    buyer_m = owner_membership(buyer)

    items = [
        make_item(
            seller,
            product=make_product(seller, commercial_name=f"سنگ شماره {index}"),
            lot_code=f"Q-{index}",
            b2b="1000000",
            b2c="1500000",
        )
        for index in range(ROWS)
    ]

    for index, item in enumerate(items):
        create_purchase_request(
            buyer_business=buyer,
            membership=buyer_m,
            item=item,
            requested_qty_sqm=Decimal("10"),
            proposed_unit_price=Decimal("1000000"),
        )
        create_inquiry(
            business=seller,
            name=f"مشتری {index}",
            phone=f"091211100{index:02d}",
            items=[{"item": item, "quantity": Decimal("5")}],
        )
        record_direct_sale(
            seller_business=seller,
            membership=seller_m,
            item=item,
            quantity_sqm=Decimal("5"),
            unit_price=Decimal("1000000"),
            buyer_business=buyer,
        )
        # A walk-in document alongside the colleague sale above, so the invoice
        # list has both counterparty kinds to render.
        create_manual_invoice(
            business=seller,
            membership=seller_m,
            lines=[{"product_name": item.product.commercial_name, "quantity": Decimal("5"),
                    "unit_price": Decimal("1000000"), "item": item}],
            customer_name=f"مشتری {index}",
        )

    return {"seller": seller, "buyer": buyer, "items": items}


def _login(client, business):
    client.force_login(business.memberships.get(role="owner").user)
    session = client.session
    session["current_business_id"] = str(business.id)
    session.save()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("url_name", "budget"),
    [
        # Measured counts are 5-8. The budget leaves room for a session or shell
        # change without leaving room for eight extra per-row queries.
        ("inventory:lot_list", 12),
        ("marketplace:home", 12),
        ("trading:received_list", 12),
        ("trading:trade_list", 12),
        ("invoicing:list", 12),
        ("inquiries:inbox", 12),
        ("inquiries:leads", 12),
        ("businesses:colleagues", 12),
        ("accounting:index", 12),
        ("catalog_manage:list", 12),
    ],
)
def test_list_pages_do_not_scale_queries_with_rows(client, busy, url_name, budget, django_assert_max_num_queries):
    _login(client, busy["seller"])
    with django_assert_max_num_queries(budget):
        response = client.get(reverse(url_name))
    assert response.status_code == 200


@pytest.mark.django_db
def test_public_search_does_not_scale_queries_with_rows(client, busy, django_assert_max_num_queries):
    with django_assert_max_num_queries(12):
        response = client.get(reverse("catalog:public_search"))
    assert response.status_code == 200


@pytest.mark.django_db
def test_the_storefront_does_not_scale_queries_with_rows(client, busy, django_assert_max_num_queries):
    with django_assert_max_num_queries(12):
        response = client.get(
            reverse("catalog:storefront", kwargs={"business_slug": busy["seller"].slug})
        )
    assert response.status_code == 200


@pytest.mark.django_db
def test_a_shared_catalog_does_not_scale_queries_with_rows(client, busy, django_assert_max_num_queries):
    from apps.catalog.models import CustomCatalog
    from apps.catalog.services import create_custom_catalog

    catalog = create_custom_catalog(
        business=busy["seller"],
        membership=owner_membership(busy["seller"]),
        title="همه محصولات",
        mode=CustomCatalog.Mode.RULE,
        rules={"stone_type": "تراورتن"},
    )
    url = reverse("catalog:shared_catalog", kwargs={"share_token": catalog.share_token})
    with django_assert_max_num_queries(12):
        assert client.get(url).status_code == 200


@pytest.mark.django_db
def test_reports_are_aggregates_not_row_scans(client, busy, django_assert_max_num_queries):
    """Every report is a database aggregate; none walks the rows in Python."""
    _login(client, busy["seller"])
    for key in ("summary", "by_colleague", "by_stone_type", "by_product", "invoices"):
        with django_assert_max_num_queries(14):
            assert client.get(reverse("reporting:report", kwargs={"key": key})).status_code == 200
