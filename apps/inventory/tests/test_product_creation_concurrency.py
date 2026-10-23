"""Real contention for same-named products, with shared and distinct tokens."""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from django.db import connection
from django.db.models.query import QuerySet

from apps.core.testing import make_business, owner_membership
from apps.inventory.models import Product, VocabularyTerm
from apps.inventory.services import create_product_item
from apps.pricing.services import ensure_default_tiers

pytestmark = [pytest.mark.concurrency, pytest.mark.django_db(transaction=True)]


@pytest.mark.parametrize("shared_token", [True, False])
def test_simultaneous_same_named_products_retry_slug_collisions(monkeypatch, shared_token):
    business = make_business(name="ثبت همزمان", owner_phone="09120003820")
    membership = owner_membership(business)
    stone, _ = VocabularyTerm.objects.get_or_create(
        kind="stone_type", name="تراورتن", defaults={"code_prefix": "T"},
    )
    ensure_default_tiers()
    barrier = threading.Barrier(2, timeout=15)
    local = threading.local()
    original = QuerySet.exists

    def synchronized_slug_check(queryset):
        exists = original(queryset)
        if queryset.model is Product and not getattr(local, "checked", False):
            local.checked = True
            barrier.wait()
        return exists

    monkeypatch.setattr(QuerySet, "exists", synchronized_slug_check)
    token = uuid.uuid4()

    def submit(index):
        try:
            return create_product_item(
                business=business, membership=membership,
                product_fields={"stone": stone, "name_suffix": "محصول هم‌نام"},
                item_fields={"width_cm": Decimal("40")},
                b2b_price={"amount": Decimal("1000")},
                b2c_price={"amount": Decimal("2000")},
                submission_id=token if shared_token or index == 0 else uuid.uuid4(),
            ).pk
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit, index) for index in range(2)]
        ids = [future.result(timeout=30) for future in futures]
    expected = 1 if shared_token else 2
    assert len(set(ids)) == business.lots.count() == business.products.count() == expected
    assert all(lot.prices.count() == 2 for lot in business.lots.all())
