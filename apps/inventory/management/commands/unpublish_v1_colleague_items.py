"""Withdraw items that an early build of the V1 to V2 migration published.

`inventory.0005` originally mapped the old `colleagues` visibility to
`is_visible=True`, which publishes to the public web items a seller had
deliberately kept to the B2B marketplace. That mapping is now conservative, so a
database migrated from this point on is never widened.

A database that ran the *earlier* version of that migration cannot be corrected
by a forward migration: `inventory.0006` drops the `visibility` column, so by the
time a corrective migration could run there is no longer any record of which
items were `colleagues` and which were `public`. There is nothing to distinguish
them by, and guessing would either leave the disclosure in place or withdraw
products the seller had always sold publicly.

This command is therefore the deliberate, operator-driven correction: it
unpublishes items for the Businesses an operator names, having established from
a pre-migration backup which sellers were affected. It is not run automatically
and it is not a migration, because the decision it encodes belongs to a person
with the old data in front of them.

    python manage.py unpublish_v1_colleague_items --business <slug> --dry-run
    python manage.py unpublish_v1_colleague_items --business <slug>
    python manage.py unpublish_v1_colleague_items --item-codes-from codes.txt

See docs/v2-migration-strategy.md §5.
"""

from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.businesses.models import Business
from apps.inventory.models import InventoryLot


class Command(BaseCommand):
    help = "Unpublish items wrongly made public by an early V1→V2 visibility mapping."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--business",
            action="append",
            default=[],
            metavar="SLUG_OR_UUID",
            help="Restrict to one Business. Repeatable.",
        )
        parser.add_argument(
            "--item-codes-from",
            metavar="PATH",
            help="A file of lot codes, one per line, identified from a pre-migration backup.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change and write nothing.",
        )

    def handle(self, *args, **options):
        businesses = [self._business(value) for value in options["business"]]
        codes = self._codes(options.get("item_codes_from"))

        if not businesses and not codes:
            raise CommandError(
                "Refusing to unpublish everything. Name the affected Businesses with "
                "--business, or supply --item-codes-from, having established the list "
                "from a pre-migration backup."
            )

        qs = InventoryLot.objects.filter(is_visible=True, deleted_at__isnull=True)
        if businesses:
            qs = qs.filter(business__in=businesses)
        if codes:
            qs = qs.filter(lot_code__in=codes)

        affected = list(qs.select_related("business").order_by("business__name", "lot_code"))
        for lot in affected:
            self.stdout.write(f"  {lot.business.name} · {lot.lot_code} · {lot.product_id}")

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING(f"dry run: {len(affected)} item(s) would be unpublished"))
            return

        with transaction.atomic():
            count = qs.update(is_visible=False)
        self.stdout.write(
            self.style.SUCCESS(
                f"{count} item(s) unpublished. The sellers keep them and can republish "
                "under the V2 visibility rule whenever they choose."
            )
        )

    def _business(self, value: str) -> Business:
        business = Business.objects.filter(slug=value).first()
        if business is None:
            business = Business.objects.filter(pk=value).first() if _looks_like_uuid(value) else None
        if business is None:
            raise CommandError(f"No Business matches {value!r}.")
        return business

    def _codes(self, path: str | None) -> list[str]:
        if not path:
            return []
        source = Path(path)
        if not source.exists():
            raise CommandError(f"No such file: {path}")
        return [line.strip() for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]


def _looks_like_uuid(value: str) -> bool:
    from uuid import UUID

    try:
        UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True
