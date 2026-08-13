"""The V1 to V2 upgrade must not publish anything on a seller's behalf.

Collapsing `private` / `colleagues` / `public` into one boolean is not
information-preserving, and the direction of the loss matters. Old `colleagues`
meant "the B2B marketplace, never the public web". Mapping it to the new
`is_visible=True` would have made those items — their existence, images,
specifications and B2C price — discoverable by anyone, on the seller's behalf and
without their consent.

These tests drive the real migration graph backwards to the pre-V2 schema, write
rows in every legacy shape, and migrate forward again. Asserting on the migration
function directly would prove only that the constant is read.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

BEFORE = ("inventory", "0004_item_lifecycle_fields")
AFTER = ("inventory", "0005_backfill_item_lifecycle")
HEAD = ("inventory", "0008_lotmedia_terminology")

pytestmark = pytest.mark.django_db(transaction=True)


#: Apps whose schema this rewind moves. A ``businesses`` migration that depends
#: on any of them cannot stay applied while inventory goes backwards.
_REWOUND = ("inventory", "trading", "invoicing")


def _businesses_target(loader) -> str:
    """The newest ``businesses`` migration that survives rewinding inventory.

    Rewinding inventory alone leaves the businesses *table* at head while the
    project state describes businesses at 0001, so writing a Business fails on
    columns the historical model does not know about — ``plan`` and
    ``seat_limit`` among them. Naming a businesses target too keeps schema and
    state describing the same database.

    It cannot simply be the head: later businesses migrations depend on invoicing,
    which depends on inventory, and holding those forward while inventory goes
    backwards is a plan Django refuses to run. So: the newest one that does not
    reach into anything being rewound.
    """
    candidates = sorted(name for app, name in loader.graph.nodes if app == "businesses")
    for name in reversed(candidates):
        ancestors = loader.graph.forwards_plan(("businesses", name))
        if not any(app in _REWOUND for app, _ in ancestors):
            return name
    raise AssertionError("no businesses migration is independent of the inventory graph")


def _targets(inventory_migration: str) -> list[tuple[str, str]]:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    return [
        ("inventory", inventory_migration),
        ("businesses", _businesses_target(executor.loader)),
    ]


def _migrate(inventory_migration: str) -> object:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    targets = _targets(inventory_migration)
    executor.migrate(targets)
    executor.loader.build_graph()
    return executor.loader.project_state(targets).apps


@pytest.fixture
def legacy():
    """Rewind to the pre-backfill schema and hand back its model registry."""
    apps = _migrate(BEFORE[1])
    yield apps
    # Leave the database at head, or every later test in the process runs against
    # a half-migrated schema.
    _migrate(HEAD[1])


def _seed(apps, *, visibility: str, status: str) -> str:
    Business = apps.get_model("businesses", "Business")
    Warehouse = apps.get_model("businesses", "Warehouse")
    Product = apps.get_model("inventory", "Product")
    InventoryLot = apps.get_model("inventory", "InventoryLot")

    # Every field spelled out: historical models carry no custom save() and no
    # Python-level defaults added by a later AlterField.
    business = Business.objects.create(
        name=f"سنگ {visibility}-{status}",
        slug=f"biz-{visibility}-{status}",
        city="اصفهان",
        province="اصفهان",
        plan="seller",
        seat_limit=1,
        status="active",
        verification_status="unverified",
    )
    warehouse = Warehouse.objects.create(business=business, name="انبار", city="اصفهان", address="خیابان")
    product = Product.objects.create(
        business=business,
        commercial_name=f"تراورتن {visibility}",
        slug=f"stone-{visibility}-{status}",
        stone_type="تراورتن",
    )
    code = f"L-{visibility}-{status}"
    InventoryLot.objects.create(
        business=business,
        product=product,
        warehouse=warehouse,
        lot_code=code,
        visibility=visibility,
        status=status,
        available_sqm="100",
        original_sqm="100",
        stock_valid_for_days=7,
    )
    return code


def _visibility_after(apps_before, code: str) -> bool:
    apps_after = _migrate(AFTER[1])
    InventoryLot = apps_after.get_model("inventory", "InventoryLot")
    return InventoryLot.objects.get(lot_code=code).is_visible


@pytest.mark.parametrize(
    ("visibility", "expected"),
    [
        # The only thing the seller had already chosen to show the public.
        ("public", True),
        # B2B-only. Publishing it is a disclosure the seller never agreed to.
        ("colleagues", False),
        ("private", False),
    ],
)
def test_only_previously_public_items_stay_published(legacy, visibility, expected):
    code = _seed(legacy, visibility=visibility, status="available")
    assert _visibility_after(legacy, code) is expected


def test_a_draft_is_never_published_whatever_its_visibility_said(legacy):
    code = _seed(legacy, visibility="public", status="draft")
    assert _visibility_after(legacy, code) is False


def test_a_hidden_item_is_never_published(legacy):
    code = _seed(legacy, visibility="public", status="hidden")
    assert _visibility_after(legacy, code) is False


@pytest.mark.parametrize("status", ["sold", "expired"])
def test_a_withdrawn_item_becomes_unavailable_rather_than_hidden(legacy, status):
    """Availability and visibility are separate axes, and the migration is where
    the old overloaded status field is split between them."""
    code = _seed(legacy, visibility="public", status=status)

    apps_after = _migrate(AFTER[1])
    lot = apps_after.get_model("inventory", "InventoryLot").objects.get(lot_code=code)

    assert lot.availability_status == "unavailable"
    assert lot.is_visible is True, "still published, just not currently offered"


def test_every_migrated_item_gets_its_own_share_token(legacy):
    codes = [
        _seed(legacy, visibility="public", status="available"),
        _seed(legacy, visibility="colleagues", status="available"),
        _seed(legacy, visibility="private", status="available"),
    ]
    apps_after = _migrate(AFTER[1])
    InventoryLot = apps_after.get_model("inventory", "InventoryLot")

    tokens = [InventoryLot.objects.get(lot_code=code).public_token for code in codes]
    assert all(tokens)
    assert len(set(tokens)) == len(tokens)


def test_location_is_carried_over_from_the_warehouse_being_retired(legacy):
    code = _seed(legacy, visibility="public", status="available")
    apps_after = _migrate(AFTER[1])
    lot = apps_after.get_model("inventory", "InventoryLot").objects.get(lot_code=code)
    assert lot.location_city == "اصفهان"
    assert lot.location_address == "خیابان"
