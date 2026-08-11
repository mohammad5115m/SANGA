from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.pricing.services import resolve_visible_prices


class FakeTier:
    def __init__(self, code: str, is_active: bool = True):
        self.code = code
        self.is_active = is_active


class FakePrice:
    def __init__(self, code: str, amount: str, unit: str = "per_sqm"):
        self.tier = FakeTier(code)
        self.amount = Decimal(amount)
        self.currency = "IRR"
        self.unit = unit


class FakePriceQuery(list):
    def select_related(self, *args, **kwargs):
        return self

    def filter(self, **kwargs):
        codes = set(kwargs.get("tier__code__in", []))
        active = kwargs.get("tier__is_active")
        items = [p for p in self if p.tier.code in codes]
        if active is True:
            items = [p for p in items if p.tier.is_active]
        return FakePriceQuery(items)


@pytest.mark.parametrize(
    ("audience", "expected"),
    [
        ("b2c_public", {"b2c"}),
        ("b2b_partner", {"b2b"}),
        ("owner_staff", {"b2b", "b2c"}),
    ],
)
def test_resolve_visible_prices_never_leaks_disallowed_tiers(audience, expected):
    lot = SimpleNamespace(
        prices=FakePriceQuery(
            [
                FakePrice("b2b", "1000000"),
                FakePrice("b2c", "1600000"),
            ]
        )
    )
    visible = resolve_visible_prices(lot, audience)
    assert set(visible.keys()) == expected
    assert "b2b" not in visible or audience in {"owner_staff", "b2b_partner", "platform_admin"}
    if audience == "b2c_public":
        assert "b2b" not in visible


def test_resolve_uses_prefetch_cache_and_still_filters_tiers():
    class FakePrefetchedManager(list):
        def all(self):
            return list(self)

    lot = SimpleNamespace(
        prices=FakePrefetchedManager(
            [
                FakePrice("b2b", "1000000"),
                FakePrice("b2c", "1600000"),
            ]
        ),
        _prefetched_objects_cache={"prices": True},
    )
    visible = resolve_visible_prices(lot, "b2c_public")
    assert set(visible.keys()) == {"b2c"}
