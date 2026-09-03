"""Give every existing Trade the line it always implicitly had.

Before ``TradeItem`` a Trade *was* its single line: the product name, grade,
quantity and unit price lived on the header. Reads have now moved to the lines,
so every historical row needs one or it would render as a sale of nothing.

The header columns are deliberately left in place and left populated. They are
what a one-line sale still writes, so nothing that already reads them breaks, and
keeping them means this migration is reversible without losing anything.

Nothing here recomputes money. ``line_total`` is taken from the trade's own
``total_amount`` rather than from ``quantity × unit_price``, because a historical
total is a commercial fact and a rounding difference discovered years later is
not a licence to change what somebody was invoiced. Where the two genuinely
disagree the trade is reported instead of quietly adjusted.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.db import migrations

CENT = Decimal("0.01")


def backfill(apps, schema_editor):
    Trade = apps.get_model("trading", "Trade")
    TradeItem = apps.get_model("trading", "TradeItem")

    existing = set(TradeItem.objects.values_list("trade_id", flat=True))
    lines = []
    mismatches = []

    for trade in Trade.objects.exclude(pk__in=existing).iterator():
        quantity = trade.quantity_sqm
        unit_price = trade.unit_price
        total = trade.total_amount or Decimal("0")

        # A trade with no quantity predates nothing SANGA ever wrote, but a
        # hand-edited or partially migrated database can still hold one. Give it
        # a line that carries the money, which is the part that must not vanish.
        if quantity is None or quantity <= 0:
            quantity = Decimal("1.000")
            unit_price = total
        if unit_price is None:
            unit_price = (total / quantity).quantize(CENT) if quantity else Decimal("0")

        computed = (Decimal(quantity) * Decimal(unit_price)).quantize(CENT)
        if computed != total.quantize(CENT):
            mismatches.append((str(trade.pk), str(computed), str(total)))

        lines.append(
            TradeItem(
                id=uuid.uuid4(),
                trade_id=trade.pk,
                item_id=trade.item_id,
                product_name=trade.product_name or "—",
                stone_type=trade.stone_type or "",
                grade=trade.grade or "",
                quantity=quantity,
                unit="متر مربع",
                unit_price=unit_price,
                # The header total wins. See the module docstring.
                line_total=total.quantize(CENT),
                sort_order=0,
            )
        )

    TradeItem.objects.bulk_create(lines, batch_size=500)

    if mismatches:
        # Printed, not raised: refusing to migrate would strand the deployment
        # over rows whose money is being preserved exactly as recorded.
        print(
            f"\n  note: {len(mismatches)} trade(s) had quantity × unit price disagreeing with "
            f"the recorded total; the recorded total was kept. First few: {mismatches[:5]}"
        )

    _assert_totals_preserved(Trade, TradeItem)


def _assert_totals_preserved(Trade, TradeItem):
    """Every trade must end up with lines summing to its recorded total.

    This is the check the migration exists to survive: a silent change to a
    historical commercial value is worse than a failed deploy.
    """
    from django.db.models import Sum

    drifted = []
    totals = dict(
        TradeItem.objects.values_list("trade_id").annotate(total=Sum("line_total")).values_list(
            "trade_id", "total"
        )
    )
    for trade in Trade.objects.values("pk", "total_amount").iterator():
        recorded = (trade["total_amount"] or Decimal("0")).quantize(CENT)
        summed = (totals.get(trade["pk"]) or Decimal("0")).quantize(CENT)
        if recorded != summed:
            drifted.append((str(trade["pk"]), str(recorded), str(summed)))

    if drifted:
        raise RuntimeError(
            "refusing to complete: trade line totals do not reproduce the recorded "
            f"trade totals for {len(drifted)} trade(s). First few: {drifted[:5]}"
        )


def unbackfill(apps, schema_editor):
    """Drop the lines again.

    Safe only because the header columns were never removed: the commercial facts
    still live there for every trade this migration created a line for.
    """
    apps.get_model("trading", "TradeItem").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("trading", "0003_trade_items")]

    operations = [migrations.RunPython(backfill, unbackfill)]
