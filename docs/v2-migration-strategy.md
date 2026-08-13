# SANGA V2 — Migration Strategy and Implementation Notes

This document records the state of the codebase at the start of the V2 refactor and
the rules every subsequent migration must follow. It is written for the engineer who
has to reason about a half-migrated database, not as a marketing summary.

## 1. Verified baseline (before any V2 change)

Measured on the merge-base commit, not copied from older documentation:

| Check | Result |
| --- | --- |
| `pytest` | 251 passed, 0 failed (15 test files) |
| `python manage.py check` | clean |
| `python manage.py makemigrations --check` | no changes detected |
| `python manage.py migrate` on an empty database | succeeds |
| `ruff check .` | clean (config added in this phase) |

Every phase must leave all five green. The fresh-database migrate is the one most
likely to break silently, because retiring an app removes migrations that other apps
still depend on.

## 2. Where the pre-V2 documentation was accurate

The pre-V2 docs were not lying about the code; they described the product being
replaced. `docs/product.md` listed the Demand Board as a core pillar and invoicing as
a non-goal, and both statements were true of the code at that time. The V2 work
changes the product, and the docs change with it — phase by phase, in the same commit
as the code.

Two genuine inaccuracies existed and are corrected during the refactor:

- `templates/businesses/onboarding_done.html` still advertised «ثبت اولین محموله
  (به‌زودی)» and «فاز ۲» long after the inventory wizard shipped.
- `docs/architecture.md` described a `SearchService` that was never built.

## 3. Assumptions that did not survive contact with the code

Recorded so nobody re-plans work that is already done:

- **Multi-image and multi-video support already existed.** `LotMedia` has `kind`,
  `sort_order`, `is_primary` and `caption`, with no per-item limit. What was missing
  was the management UI and real upload validation, not the model.
- **`is_urgent_sale` was never a special sale.** It is a boolean sort flag with no
  price attached. Special-sale pricing is genuinely new in V2.
- **`matching`, `reservations` and `partners` were already removed.** They are
  migration-only packages with no `models.py`. The remaining task is to *keep* them
  installed so fresh migrates work.
- **`PurchaseRequest.Status.MATCHING` was already dead.** Nothing set it; it was only
  read. Removing it is an enum cleanup, not a workflow removal.

## 4. Migration rules

Every structural change follows the same four steps, and steps 3 and 4 land in
*different* commits from steps 1 and 2:

1. Add the new schema, nullable, alongside the old.
2. Backfill with an explicit data migration.
3. Switch application reads and writes to the new schema.
4. Drop the obsolete columns/models in a later migration.

No manual database editing. No `RunSQL` where an ORM operation will do.

### 4.1 Ledger rows are immutable in application code but writable in migrations

`LedgerEntry.save()` raises on update and `delete()` raises unconditionally. Django's
historical models inside migrations strip custom methods, so a data migration *can*
rewrite these rows — and it is the only place that can.

This is load-bearing: the ledger counterparty backfill must live in a migration. An
equivalent management command would fail at the first `save()`.

`balance_after` is a stored running total. Backfills may re-point the counterparty FK;
they must never recompute balances.

### 4.2 The contacts app can never be uninstalled

`pricing.0002_contactprice` depends on `contacts.0002`, and `LedgerEntry.contact` is
`on_delete=PROTECT`. Removing `apps.contacts` from `INSTALLED_APPS` breaks `migrate`
on a fresh database.

`apps.contacts` therefore becomes a migration-only stub, exactly like `apps.partners`,
`apps.matching` and `apps.reservations`. Its rows stay for historical reference; its
UI and services go.

### 4.3 Unmappable ledger counterparties are preserved, not guessed

`LedgerEntry.contact` migrates to `counterparty_business` only where
`Contact.linked_business` is set. Everything else keeps its identity in
`legacy_counterparty_name`, copied from `Contact.display_name`.

Fabricating a counterparty for an unmappable row would corrupt a colleague's balance.
Leaving the row queryable under its legacy name does not.

### 4.4 Warehouse removal spans several migrations

`InventoryLot.warehouse` was a non-nullable `PROTECT` FK. The sequence is: add
location fields, backfill from `Warehouse.city`/`address`, make the FK nullable, stop
writing it, remove the UI, and only then drop the column. The `Warehouse` model
outlives its UI by several phases.

### 4.5 Capability codes are materialized, not computed

`BusinessMembership.permissions` is a JSON list frozen on first save. Renaming a
capability silently revokes access for every existing member, because nothing
recomputes the list. Every capability change ships with a data migration that rewrites
the stored lists.

### 4.6 Renaming a model must be its own migration

Six apps hold FKs to the inventory item model. `migrations.RenameModel` repoints them
atomically, but only when the migration contains nothing else.

## 5. Visibility collapse — a deliberate, non-reversible product decision

Collapsing `private` / `colleagues` / `public` into a single `is_visible` boolean is
not information-preserving.

Before V2, `colleagues` meant "B2B marketplace only, never the public storefront". In
V2, `is_visible=True` means discoverable by colleagues *and* the public, with audience
rules deciding what each one sees.

The chosen mapping is:

| Old visibility | New `is_visible` |
| --- | --- |
| `private` | `False` |
| `colleagues` | `False` |
| `public` | `True` |

**Why `colleagues → False`.** Old `colleagues` meant "the B2B marketplace, never
the public storefront". Mapping it to `True` would have made those items — their
existence, images, specifications and B2C price — discoverable by anyone, on the
seller's behalf and without their consent. B2B prices would have stayed protected
(the public payload is restricted to the `b2c` tier at two independent layers: the
audience gate in `pricing.services` and the tier-scoped prefetch in the query
layer), but the rest is a real widening of audience.

A migration may not make that decision. Consent to publish is the seller's to
give, and an opt-out they were never shown is not consent. The conservative
mapping costs those sellers a re-publish; the permissive one costs them a
disclosure they cannot take back.

The mapping is a single module-level constant in `inventory.0005`, and
`apps/inventory/tests/test_migrations.py` drives the real migration graph
backwards to the pre-V2 schema and forwards again for every legacy
visibility/status combination.

### Correcting a database that ran the earlier mapping

`inventory.0006` drops the `visibility` column, so by the time a corrective
migration could run there is no record of which items were `colleagues` and which
were `public`. Nothing distinguishes them, and guessing would either leave the
disclosure in place or withdraw products the seller had always sold publicly.

The correction is therefore operator-driven, not automatic:

```bash
python manage.py unpublish_v1_colleague_items --business <slug> --dry-run
python manage.py unpublish_v1_colleague_items --business <slug>
```

The affected sellers are established from a pre-migration backup. The command
refuses to run without a `--business` or `--item-codes-from` argument, because
"unpublish everything" is not a correction. Items are unpublished, never deleted:
the seller keeps them and republishes under the V2 rule when they choose to.

No production database is affected. `master` — the deployed V1 — stops at
`inventory.0003`; `0005` has only ever existed on the V2 branch.

## 6. Architectural keystone: one eligibility policy, one filter schema

Before V2, the question "may this viewer see this item?" was answered by three
near-duplicate functions that had already drifted apart:

- `marketplace_lots_for()` in `apps/marketplace/selectors.py`
- `public_catalog_lots()` in `apps/catalog/selectors.py`
- an inline loop in `shared_catalog()` in `apps/catalog/views_public.py`

The third one checked `status` but forgot `visibility`. That drift *was* the P0 catalog
leak: a private item attached to a custom catalog rendered publicly.

V2 replaces all three with:

- `apps/inventory/policy.py` — `eligible_items()`, the only definition of a
  buyer-visible item.
- `apps/inventory/filters.py` — `ItemFilterSpec`, one serializable filter schema shared
  by my-inventory, the marketplace, public search and catalog rules.

A rule-based catalog is literally a stored `ItemFilterSpec`. There is deliberately no
second filtering language.

## 7. Four independent lifecycle axes

The single most important modelling rule in V2. These are never merged into one status
field:

| Axis | Field | Question |
| --- | --- | --- |
| Visibility | `is_visible` | Should the seller publish this at all? |
| Availability | `availability_status` | Is it offered for sale right now? |
| Stock freshness | `stock_confirmed_at` + `stock_valid_for_days` | Do we trust the quantity? |
| Deletion | `deleted_at` | Should this still exist as an active business object? |

This makes «ناموجود» and «استعلام موجودی» structurally impossible to confuse.
`ناموجود` is `availability_status`, which removes the item from `eligible_items()`.
`استعلام موجودی` is derived from stock expiry, which does not.

Freshness is computed at read time. No scheduled job mutates rows to express it.
