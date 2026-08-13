"""Carry the old overloaded status/visibility data onto the new lifecycle axes.

Nothing is destroyed here. 0006 removes the old columns once this has run.

The visibility mapping is a deliberate, reviewed product decision rather than a
mechanical translation, so it lives in one named constant at the top of the file
where it can be found and argued with. See docs/v2-migration-strategy.md §5.
"""

import secrets
from datetime import timedelta

from django.db import migrations


def _token() -> str:
    """Deliberately duplicated from inventory.models.

    A migration must keep working after the runtime helper is renamed or
    deleted, so it cannot import one.
    """
    return secrets.token_urlsafe(12)


# Old `colleagues` meant "B2B marketplace only, never the public storefront".
# New `is_visible=True` means discoverable by colleagues *and* the public, with
# audience rules deciding what each one is shown.
#
# Mapping `colleagues -> True` therefore publishes items a seller had kept off
# the public web: their existence, images, specifications and B2C price become
# visible. B2B prices are unaffected, because the public payload is restricted
# to the b2c tier independently in pricing.services and in the query layer.
#
# Flip this to False to take the conservative route (nothing new is exposed, but
# those sellers silently drop out of the B2B marketplace until they re-publish).
COLLEAGUES_BECOME_VISIBLE = True

# Statuses that described "the seller is not offering this right now" rather
# than a step in a workflow. They become availability, not status.
UNAVAILABLE_STATUSES = {"sold", "expired"}

# Statuses that described "do not show this to buyers". They become visibility.
HIDDEN_STATUSES = {"hidden"}


def forwards(apps, schema_editor):
    InventoryLot = apps.get_model("inventory", "InventoryLot")

    updated = []
    seen_tokens = set()

    rows = InventoryLot.objects.select_related("warehouse", "business").iterator()
    for lot in rows:
        # --- visibility -------------------------------------------------------
        if lot.visibility == "public":
            is_visible = True
        elif lot.visibility == "colleagues":
            is_visible = COLLEAGUES_BECOME_VISIBLE
        else:
            is_visible = False
        # A draft or explicitly hidden item was never on a buyer surface, and
        # must not become one just because its visibility column said otherwise.
        if lot.status in HIDDEN_STATUSES or lot.status == "draft":
            is_visible = False
        lot.is_visible = is_visible

        # --- availability -----------------------------------------------------
        lot.availability_status = "unavailable" if lot.status in UNAVAILABLE_STATUSES else "available"

        # --- stock ------------------------------------------------------------
        # Every pre-V2 item carried a square-metre figure, so `exact` is the only
        # honest starting mode. Sellers opt into unlimited/inquiry afterwards.
        lot.stock_mode = "exact"
        if lot.stock_confirmed_at is not None:
            lot.stock_expires_at = lot.stock_confirmed_at + timedelta(days=lot.stock_valid_for_days)

        # --- location ---------------------------------------------------------
        # Preserve the warehouse address on the item before the Warehouse
        # relationship goes away. Discarding it would lose data the seller typed.
        warehouse = lot.warehouse
        if warehouse is not None:
            lot.location_city = warehouse.city or ""
            lot.location_address = warehouse.address or ""
        if not lot.location_city:
            lot.location_city = lot.business.city or ""
        if not lot.location_province:
            lot.location_province = lot.business.province or ""

        # --- share token ------------------------------------------------------
        token = _token()
        while token in seen_tokens:
            token = _token()
        seen_tokens.add(token)
        lot.public_token = token

        # --- status collapse --------------------------------------------------
        lot.status = "draft" if lot.status == "draft" else "active"

        updated.append(lot)

        if len(updated) >= 500:
            _flush(InventoryLot, updated)
            updated = []

    _flush(InventoryLot, updated)


def _flush(model, rows):
    if not rows:
        return
    model.objects.bulk_update(
        rows,
        [
            "is_visible",
            "availability_status",
            "stock_mode",
            "stock_expires_at",
            "location_city",
            "location_province",
            "location_address",
            "public_token",
            "status",
        ],
    )


def backwards(apps, schema_editor):
    """Reconstruct the old columns as faithfully as the collapse allows.

    Lossy on purpose: `colleagues` and `public` both mapped to `is_visible=True`
    and cannot be told apart afterwards, so everything visible comes back as
    `colleagues` — the narrower of the two, to avoid re-publishing anything.
    """
    InventoryLot = apps.get_model("inventory", "InventoryLot")

    InventoryLot.objects.filter(is_visible=True).update(visibility="colleagues", status="available")
    InventoryLot.objects.filter(is_visible=False).exclude(status="draft").update(
        visibility="private", status="hidden"
    )
    InventoryLot.objects.filter(availability_status="unavailable").update(status="sold")


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0004_item_lifecycle_fields"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
