"""The capability rename must not silently revoke anyone's access.

``BusinessMembership.permissions`` is a materialized JSON list that nothing
recomputes, so renaming a code without rewriting the stored lists is a
data-loss bug dressed up as a refactor. These tests exercise the migration
functions directly against the current models — running the historical
migration would need a second database, and the mapping logic is what can
actually be wrong.
"""

from __future__ import annotations

from importlib import import_module

import pytest

from apps.businesses.models import BusinessMembership
from apps.businesses.permissions import (
    ALL_CAPABILITIES,
    INVOICE_MANAGE,
    INVOICE_VIEW,
    LEADS_MANAGE,
    LEADS_VIEW,
    LEDGER_MANAGE,
    LEDGER_VIEW,
    PURCHASE_REQUEST,
    SALE_FINALIZE,
    defaults_for_role,
)

# Loaded by name because the module starts with a digit. Testing the migration's
# own copy of the mapping is the point: a test against permissions.py would pass
# even if the two drifted.
_MIGRATION = import_module("apps.businesses.migrations.0003_rewrite_capability_codes")


def _rewrite(codes: list[str]) -> list[str]:
    """Apply the migration's forward mapping to one permission list."""
    FORWARD, IMPLIED = _MIGRATION.FORWARD, _MIGRATION.IMPLIED

    out: list[str] = []
    for code in codes:
        mapped = FORWARD.get(code, code)
        if mapped is not None and mapped not in out:
            out.append(mapped)
        for implied in IMPLIED.get(code, ()):
            if implied not in out:
                out.append(implied)
    return out


@pytest.mark.parametrize(
    ("old", "expected_present"),
    [
        (["inquiries.view"], {LEADS_VIEW}),
        (["inquiries.respond"], {LEADS_MANAGE, PURCHASE_REQUEST, SALE_FINALIZE}),
        (["customers.manage"], {LEADS_MANAGE}),
        (["ledger.view"], {LEDGER_VIEW, INVOICE_VIEW}),
        (["ledger.manage"], {LEDGER_MANAGE, INVOICE_MANAGE}),
    ],
)
def test_renamed_capabilities_keep_their_holder_working(old, expected_present):
    assert expected_present.issubset(set(_rewrite(old)))


@pytest.mark.parametrize("dropped", ["analytics.view", "audit.view"])
def test_capabilities_that_were_never_checked_are_dropped(dropped):
    assert _rewrite([dropped]) == []


def test_rewrite_is_idempotent():
    """Re-running the migration must not duplicate or lose codes."""
    once = _rewrite(["inquiries.respond", "ledger.view"])
    assert _rewrite(once) == once


def test_rewrite_produces_no_duplicates():
    both = _rewrite(["inquiries.respond", "customers.manage"])
    assert len(both) == len(set(both))


def test_every_produced_code_still_exists():
    produced = set()
    for role in ("owner", "manager", "staff", "viewer"):
        produced.update(defaults_for_role(role))
    for legacy in ("inquiries.view", "inquiries.respond", "customers.manage", "ledger.manage"):
        produced.update(_rewrite([legacy]))
    assert produced.issubset(set(ALL_CAPABILITIES))


@pytest.mark.django_db
def test_role_defaults_are_materialized_on_save():
    from apps.core.testing import make_business, make_user

    business = make_business(name="سنگ نقش", owner_phone="09151110001")
    business.seat_limit = 5
    business.save(update_fields=["seat_limit"])

    membership = BusinessMembership.objects.create(
        user=make_user("09151110002"),
        business=business,
        role=BusinessMembership.Role.STAFF,
        status=BusinessMembership.Status.ACTIVE,
    )
    assert set(membership.permissions) == set(defaults_for_role("staff"))
    assert membership.has_capability(SALE_FINALIZE)
    assert not membership.has_capability(LEDGER_MANAGE)
