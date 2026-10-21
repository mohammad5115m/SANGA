# SANGA V2 Remediation Implementation Report

**Repository:** `mohammad5115m/SANGA`
**Branch:** `cursor/sanga-v2-remediation-b749` (based on `cursor/sanga-v2-refactor-c477`)
**Source documents:** `PRODUCT_AUDIT_REPORT.md`, `REMEDIATION_PLAN.md`
**Date:** 2026-08-13

---

## 1. Executive Summary

All 37 audit findings were located in the code and individually verified before
anything was changed. 34 were fixed, 1 was already correct as written, and 2 are
process items that require repository-owner action outside a pull request.

The work was done in ten dependency-ordered phases, one commit each, with the
full validation gate green at every step. The result is 124 files changed, 6 new
migrations, 14 new test files, and a test suite that grew from 414 to 568.

Four invariants now hold that did not before:

- **One commercial event has one representation.** A sale is a `Trade`; the
  `Trade` posts both parties' ledgers and backs at most one invoice, enforced by
  a partial unique index rather than by a lookup that cannot survive concurrency.
- **Eligibility is one question with four named answers.** Whether a tenant may
  write, appear to others, sell, or be reached as a historical counterparty were
  being answered by whatever check was nearest, and had drifted. They are now
  four predicates in one module, each with a SQL twin so a queryset and an `if`
  cannot disagree.
- **Public inquiries have one entry point.** Every path — product page, shared
  catalog, stale-stock question, search results — runs the same select, quantity,
  identity, OTP pipeline, and the whole submission is one transaction keyed by a
  token minted before the code is sent.
- **Production fails closed.** A misconfigured `SMS_PROVIDER`, a development
  `ALLOWED_HOSTS`, or a wildcard host stops the process at import rather than
  producing a deployment that looks healthy and logs every login code to disk.

Two audit recommendations were deliberately not followed as written, because
following them would have contradicted a documented product rule or shipped a
policy change disguised as a bug fix. Both are argued in §6.

**Readiness: ready for controlled staging with a realistic dataset, not yet for
general availability.** The reasoning is in §14.

---

## 2. Audit Issue Status

| ID | Severity | Status | Implementation | Tests |
|----|----------|--------|----------------|-------|
| AUD-001 | P1 | FIXED | `uniq_invoice_per_trade` partial index; `create_invoice_for_trade` locks the seller before checking, rechecks under the lock, and resolves `IntegrityError` to the winner | `test_one_trade_can_never_have_two_invoices`, `test_two_threads_invoicing_one_trade_produce_one_document` (PG) |
| AUD-002 | P1 | FIXED | `create_manual_invoice` refuses a Business counterparty; new «ثبت فروش مستقیم» at `/app/trading/direct-sale/` creates Trade → ledger → invoice in one transaction | `test_a_colleague_invoice_cannot_be_typed_by_hand`, `test_a_direct_colleague_sale_produces_trade_ledger_and_invoice_together` |
| AUD-003 | P1 | FIXED | `post_trade_entries` posts seller `SALE` and buyer `PURCHASE` together, locking both Business rows in stringified-UUID order, idempotent per side | `test_finalizing_a_sale_moves_the_buyers_books_too`, `test_two_threads_posting_one_trade_move_each_book_once` (PG) |
| AUD-004 | P1 | FIXED (scope adjusted, §6) | `require_operational()` in every tenant write service; suspension banner; own-data reads preserved per `docs/permissions.md` §8 | `test_a_suspended_tenant_cannot_edit_its_own_products`, `test_a_suspended_tenant_still_reads_its_own_records` |
| AUD-005 | P1 | FIXED | `eligible_items` joins `can_sell_q("business")` — active, current subscription, verified-enough, selling plan | `test_a_downgraded_seller_disappears_from_every_buyer_surface` |
| AUD-006 | P1 | FIXED (as denylist, §6) | `UNTRUSTED_VERIFICATION_STATES` removes REJECTED/SUSPENDED from the network; `SANGA_REQUIRE_VERIFIED_FOR_NETWORK` flips to allowlist | `test_a_refused_seller_disappears_from_every_buyer_surface`, `test_an_unverified_business_still_participates` |
| AUD-007 | P1 | FIXED | `accounting_counterparty()` resolves through commercial history *or* directory eligibility | `test_a_settlement_can_be_recorded_against_a_suspended_debtor` (+6) |
| AUD-008 | P1 | FIXED | `permissions` is nullable; `None` means "decide for me", `[]` means what it says | `test_stripping_an_existing_members_permissions_is_not_undone` |
| AUD-009 | P1 | FIXED | Product-detail and shared-catalog inquiry forms removed; both views GET-only; all entry points funnel through the OTP pipeline | `test_the_product_page_cannot_record_an_inquiry_directly` |
| AUD-010 | P1 | FIXED | `submit_public_inquiry()` in one transaction keyed by `submission_id`, unique per seller; notifications `on_commit` | `test_a_failure_on_the_second_seller_leaves_nothing_behind`, `test_a_double_submitted_form_records_one_inquiry_per_seller` (PG) |
| AUD-011 | P1 | FIXED | Wizard hides and skips validating the price step for members without `prices.edit`; creation and pricing are one service call; trade-driven invoice needs only the Business entitlement and yields a draft | 9 tests in `test_staff_workflows.py` |
| AUD-012 | P1 | FIXED | `apps/inventory/queries.py` + `apps/pricing/queries.py`; filters and sorting use effective stock and effective price | 17 tests in `test_freshness_filters.py` |
| AUD-013 | P1 | FIXED | `apps/core/pagination.py` on 10 surfaces; filters and sort preserved in the pager link | 15 tests in `test_pagination.py` |
| AUD-014 | P1 | FIXED | `COLLEAGUES_BECOME_VISIBLE = False`; correction command for staging; no deployed database ever ran it (`master` stops at `inventory.0003`) | 9 tests in `test_migrations.py`, driving the real graph backwards and forwards |
| AUD-015 | P1 | FIXED | Provider registry; production rejects unknown or non-delivering providers; SMS logger silenced in production | `test_an_unknown_provider_is_refused_rather_than_guessed`; verified by hand (§11) |
| AUD-016 | P2 | FIXED | `stock_valid_for_days` passed from `lot_confirm_stock` to `confirm_item_stock` and validated | `test_the_confirmation_form_persists_the_validity_it_was_given` |
| AUD-017 | P2 | FIXED | `_claim_challenge()` locks the row, increments with `F()`, burns conditionally, raises after commit; per-IP throttle added | 3 PG concurrency tests + 9 in `test_otp_hardening.py` |
| AUD-018 | P2 | FIXED | `apps/inventory/media_validation.py`: Pillow decode for images, container signature for video; size checked before decoding | `test_a_renamed_file_is_refused_however_it_labels_itself` (+5) |
| AUD-019 | P2 | FIXED | `schedule_storage_cleanup()` deletes file and thumbnail via `transaction.on_commit` | `test_deleting_media_deletes_the_stored_object`, `test_a_rolled_back_delete_leaves_the_object_alone` |
| AUD-020 | P2 | FIXED | `uniq_primary_media_per_lot` partial index; item row locked while the cover changes | `test_the_database_refuses_a_second_primary_image`, 2 PG concurrency tests |
| AUD-021 | P2 | FIXED | `invoices_for()` shows buyers ISSUED and CANCELLED only | `test_a_buyer_cannot_list_a_draft` (+3) |
| AUD-022 | P2 | FIXED | `Business.invoice_sequence`, incremented under the existing lock; migration seeds from history | `test_numbers_stay_sequential_and_are_never_reused` |
| AUD-023 | P2 | FIXED | `HistoricalRecordAdmin`; Trade always read-only and uncreatable, invoices read-only once issued or cancelled, neither deletable | 7 tests in `test_admin_safety.py` |
| AUD-024 | P2 | FIXED | `BusinessAdmin` cannot delete; suspension is the supported operation | `test_admin_refuses_to_delete_commercial_records[Business]` |
| AUD-025 | P2 | FIXED | `business_context` exposes `can_add_products`, `can_manage_catalogs`, `can_finalize_sales`, `can_issue_invoices` — capability *and* entitlement — plus a `business_block_reason` banner | Covered by the eligibility matrix |
| AUD-026 | P2 | FIXED | `resolve_catalog()` composes `Q` objects and returns a queryset; manual order is a `Case` expression | `test_resolving_a_rule_catalog_does_not_load_every_match` |
| AUD-027 | P2 | PARTIALLY FIXED | `.github/workflows/ci.yml` with 6 jobs. Branch protection itself needs a repository setting only an owner can apply | CI is the verification mechanism |
| AUD-028 | P2 | FIXED | `scripts/run_pg_tests.sh`, `@pytest.mark.concurrency`, auto-skip on SQLite, CI asserts they actually ran | 10 concurrency tests |
| AUD-029 | P2 | FIXED | Multi-stage non-root Gunicorn image, `entrypoint.sh` with four roles, `docker-compose.prod.yml`, migrations as a release step | Build-file review; Docker unavailable in this environment (§12) |
| AUD-030 | P2 | FIXED | Alpine and HTMX self-hosted and pinned; inline JS moved to `static/js/app.js`; `SecurityHeadersMiddleware` ships CSP with `script-src 'self'` | Verified by header inspection (§11) |
| AUD-031 | P2 | FIXED | Same pipeline as AUD-009; quantity is always collected | `test_the_product_page_button_starts_the_verified_flow` |
| AUD-032 | P2 | FIXED | `invoice_summary` sums ISSUED only, drafts subtotalled separately; statements gained an opening balance | `test_the_invoice_total_sums_issued_documents_only` |
| AUD-033 | P3 | FIXED | `logout_view` is `@require_POST`; the last GET link became a form | Existing logout tests |
| AUD-034 | P3 | FIXED | `requirements/constraints.txt`, used with `-c` in the image and CI; `pip-audit` job | Reproducible build by construction |
| AUD-035 | P3 | FIXED | DRF removed from `INSTALLED_APPS`, settings and requirements after confirming zero imports | Full suite + `manage.py check` |
| AUD-036 | P3 | FIXED | Special-sale reset is deliberate and now stated in the success message | Behaviour documented at the call site |
| AUD-037 | P3 | FIXED | `Warehouse` unregistered from admin | `test_warehouse_is_not_registered_in_admin` |

**Not applicable / already correct:** the audit's concern in AUD-036 was that the
behaviour was *undocumented*, not that it was wrong. Resetting a time-limited
promotion when duplicating an item is the correct behaviour; only the silence was
a defect, and the silence is what was fixed.

**Requires repository-owner action:** AUD-027's branch protection. The workflow
exists and runs; making `master` require it is a GitHub settings change.

---

## 3. Architecture Changes

Nothing was rewritten. Every change went into an existing service, selector or
policy boundary, or created a new module at the same layer as its neighbours.

**New modules**

| Module | Answers |
|--------|---------|
| `apps/businesses/eligibility.py` | May this tenant write / appear / sell? Python and SQL forms of each |
| `apps/inventory/queries.py` | What is this item's *current* stock, in SQL |
| `apps/pricing/queries.py` | What is this item's *current* price, in SQL |
| `apps/inventory/media_validation.py` | Is this upload actually an image or a video |
| `apps/core/pagination.py` | One pager for every list |
| `apps/core/middleware.py` | CSP, Permissions-Policy |
| `apps/core/admin.py` | Read-only, undeletable history |

**Boundaries that moved**

- `post_trade_for_sale` → `post_trade_entries`, returning a `TradePosting` with
  both sides. `_post` split into an authorizing wrapper and a raw `_write_entry`,
  because the buyer's row is written by the seller's transaction and there is no
  buyer-side membership to check.
- Invoice creation from a `Trade` no longer requires the actor's
  `invoice.manage`. It is a consequence of the sale, not a second authored
  commercial event, and requiring it meant a staff sale moved the ledger and
  silently produced no document.
- `create_draft_item` takes the price specs, so "add this product" is one
  transaction rather than two.
- Public inquiry gained `submit_public_inquiry()` above the per-seller service,
  because all-or-nothing and retry-safety are properties of the *submission*,
  which the per-seller call cannot see.

**Deliberately kept apart:** live network eligibility and historical accounting
identity. Collapsing them is what made a suspended debtor's statement 404 while
their debt stayed real.

---

## 4. Database Migrations

Six migrations, all additive; no column or table was dropped.

| Migration | Why |
|-----------|-----|
| `invoicing.0002_salesinvoice_uniq_invoice_per_trade` | One Trade, one document. Includes a guard that names any pre-existing duplicates rather than failing with an opaque `IntegrityError`; collapsing them automatically is a commercial decision, not a migration's |
| `businesses.0004_alter_businessmembership_permissions` | `permissions` nullable, so `None` ("decide for me") and `[]` ("no capabilities") stop being the same value |
| `businesses.0005_business_invoice_sequence` | Per-seller counter, **seeded from existing invoice numbers**. Starting at zero would reissue numbers that already exist |
| `inquiries.0005_inquiry_submission_id_and_more` | `submission_id` + `uniq_inquiry_per_submission_and_seller`, the invariant a retry relies on |
| `inventory.0009_lotmedia_uniq_primary_media_per_lot` | One cover per gallery, where two simultaneous uploads cannot both win |
| `inventory.0005_backfill_item_lifecycle` (edited) | `COLLEAGUES_BECOME_VISIBLE = False` |

The last one is an in-place edit of an existing migration, which normally would
not be acceptable. It is here because `git ls-tree origin/master -- apps/inventory/migrations`
shows `master` — the deployed V1 — stops at `0003`, so no database has ever run
`0005`. A forward correction is also impossible in principle: `0006` drops the
`visibility` column, so nothing afterwards can tell a `colleagues` item from a
`public` one. For a staging database that ran the earlier mapping there is
`manage.py unpublish_v1_colleague_items`, which refuses to run without being told
which sellers were affected.

---

## 5. Financial Integrity Changes

The invariant, for every sale however it was reached:

```text
sale (request-driven or direct)
  → exactly one Trade
      → exactly one live ledger entry in each participating Business
      → at most one invoice
  all in one transaction
```

**Both parties keep books.** `LedgerEntry.PURCHASE`, the business-scoped trade
constraint and the `purchased` report total were all designed two-sided; only the
seller's half was ever posted, so a buyer's purchase totals were permanently zero
while the seller's statement said money was owed. `post_trade_entries` writes
both. From the seller's books the colleague becomes بدهکار; from the buyer's, the
colleague becomes بستانکار.

The buyer's row is written without a buyer-side membership on purpose. It is not
bookkeeping the seller is authoring in someone else's name — it is the other half
of a transaction the buyer is a party to, and the buyer can reverse it from their
own statement. Idempotency is evaluated **per side**, so a party who reversed
their own entry can have it reposted without disturbing the other's book.

**Deadlock avoidance:** both `Business` rows are locked in ascending stringified
UUID order. Two trades running in opposite directions between the same pair would
otherwise each hold the row the other wants.

**Exactly-once, three layers per side:** deterministic locking, a pre-check under
those locks, and `uniq_trade_entry_per_trade` scoped by `business`.

**One authoritative workflow.** A colleague invoice now has exactly one origin: a
finalized Trade. `create_manual_invoice` refuses a Business counterparty and
points at the direct-sale page. Multi-line hand-typed invoices remain for walk-in
customers, where no account moves.

The trade-off, stated plainly: a colleague direct sale describes **one product
line**, because a Trade carries one snapshot and one Trade backs one invoice and
one ledger entry per party. A basket of different stones is several sales. The
alternative — one invoice spanning several Trades — would have broken the 1:1
relation that AUD-001 exists to establish.

---

## 6. Authorization Changes

`apps/businesses/eligibility.py` names four questions that were being answered ad
hoc:

| Predicate | Question | Used by |
|-----------|----------|---------|
| `business_can_use_app` | May this tenant **write**? | `require_operational()` in every write service |
| `business_is_network_eligible` | Should it be **visible** to others? | colleague directory |
| `business_can_sell` | May it be on the **selling** side? | `eligible_items`, purchase requests |
| `accounting_counterparty` | Did these two ever **transact**? | statements, settlements |

Each of the first three has a SQL twin (`network_eligible_q`, `can_sell_q`) in the
same module, so a queryset and an `if` cannot drift.

### Two deliberate deviations from the audit

**AUD-004 — a write gate, not a full lockout.** The audit expected a suspended
tenant to be blocked from "normal application usage". `docs/permissions.md` §8
states the opposite as a product rule: *"A suspended business keeps full access to
its own data — its products, its own records and its ledger are untouched. The
gate is on participation in the shared network."* Blocking reads would also have
directly contradicted AUD-007, which is about keeping history reachable. So the
gate is on **writing**: `require_operational()` is called from the capability
helper in every write service, so editing a product, confirming stock, uploading
media, curating a catalog and sending a purchase request all stop, while records,
invoices and the ledger stay readable and historical debts stay settleable.

**AUD-006 — verification as a denylist, not an allowlist.** The audit and the
plan recommended that only `VERIFIED` businesses join the network.
`verification_status` defaults to `unverified` and nothing in provisioning sets
it, so that change would have emptied every directory and marketplace on the day
it shipped — a policy change disguised as a bug fix, with no migration to make it
survivable. `docs/data-model.md` also calls the field *"platform trust,
deliberately independent of status"*. What the field can mean **today** is a
decision the platform has actually taken: `REJECTED` and `SUSPENDED` are explicit
refusals, and a Business carrying one is removed from everyone else's screens.
`SANGA_REQUIRE_VERIFIED_FOR_NETWORK` flips it to an allowlist in one line, for
the day the platform starts verifying and has backfilled the field.

### Membership permissions

`BusinessMembership.permissions` is nullable. `None` means "not decided yet" and
is replaced by role defaults on first save; `[]` means "no capabilities" and
survives. The old truthiness test could not tell them apart, so an admin who
stripped a member's access got the role defaults handed straight back.

### Navigation

`business_context` composes capability with entitlement. Owner capability bypass
meant checking capabilities alone left «افزودن» — the most prominent control on
the screen — pointing at a wizard the plan could not finish.

---

## 7. Public Inquiry Changes

One pipeline, four entry points:

```text
search card / product page / stale-stock button / shared catalog
  → select      (product added to the session selection)
  → quantity    (per item, on the review page)
  → identity    (name and phone)
  → OTP         (customer purpose; creates no User and no session)
  → submit      (one transaction, one inquiry per seller)
```

The product-detail and shared-catalog pages carried their own name/phone forms
that called `create_inquiry` directly, so the most obvious button on the most
visited public page recorded an inquiry with an unverified phone, no requested
quantity and — on the catalog — no product rows at all. Both are gone; both views
are GET-only.

The stale-stock question keeps its one-click button but runs the same pipeline
seeded with its own message. A low-friction exception would have left one class
of inquiry whose phone nobody had checked, and no way for a seller to tell which
kind they were looking at.

**Idempotency.** `submission_id` is minted *before* the OTP is sent and stored in
the pending session payload, so a refresh, a double-tapped submit on a slow
connection, or a retry after a failure all carry the same token.
`uniq_inquiry_per_submission_and_seller` makes it unique per seller, and the
service resolves the resulting `IntegrityError` to the winning row rather than
failing the customer.

**Atomicity.** All sellers are written in one outer transaction. The loop used to
run outside any transaction, so a failure on the third seller left the first two
committed while the page reported failure — and the customer had no way to tell
which sellers had heard them.

**Notifications** are scheduled with `transaction.on_commit`, so none is sent for
an inquiry that rolled back and a failing notification backend cannot lose one.

Customers still never become platform Users, and verification still starts no
session.

---

## 8. Security Improvements

| Area | Before | After |
|------|--------|-------|
| SMS provider | Unknown name fell back to console with a warning | Registry; unknown is an error; production refuses a non-delivering provider |
| OTP logs | Codes written to the application log in any environment | `apps.accounts.sms` silenced under production settings |
| OTP verification | Read-check-write; a one-time code could be used twice | Row locked, `F()` increment, conditional burn, refusal raised after commit |
| OTP abuse | Every limit keyed on the phone number | Per-IP hourly cap added |
| Uploads | Extension, Content-Type and guessed MIME — all caller-supplied, all derived from the filename | Images decoded by Pillow (twice); videos matched against container signature |
| Media storage | Row deleted, object left behind and still reachable | Deleted on commit, including thumbnails |
| Logout | Accepted GET | `@require_POST` |
| Frontend JS | Alpine and HTMX from `unpkg.com`, inline scripts | Self-hosted and pinned; `script-src 'self'` with nothing carved out |
| Headers | Django's `SECURE_*` only | Plus CSP, `Permissions-Policy`, `Referrer-Policy` |
| `ALLOWED_HOSTS` | Development values booted happily | Loopback-only or `*` refuses to start |
| Django Admin | Could edit a finalized Trade or delete an issued invoice | Read-only history, no deletion of Trade / Invoice / Business |
| Buyer access | Could read seller drafts | Issued and cancelled only |
| Draft visibility | — | Suspended tenants keep reads, lose writes |

Tenant isolation and B2B price confidentiality were already sound and were
preserved: the freshness rewrite kept the price annotation tier-scoped
(`test_the_b2b_price_never_answers_a_public_price_filter`), and the buyer's
`PURCHASE` ledger row deliberately carries no `related_lot`, because the product
belongs to the seller's tenant.

---

## 9. Performance Improvements

- **Pagination** replaced hard caps on 10 surfaces. Past the cap, products still
  matched the search and were simply unreachable.
- **Catalog resolution** composes `Q` objects instead of materialising every rule
  match into a Python set, and returns a queryset, so the cost of page one no
  longer grows with the size of the whole match.
- **Invoice numbering** is one `UPDATE` against a counter instead of pulling
  every number the Business ever issued into Python — while holding the lock
  every other salesperson was queued behind.
- **Price filtering** became a tier-scoped subquery rather than a join, so it can
  no longer multiply rows and needs no `.distinct()` to undo the damage.
- **Query budgets** hold. `test_query_count_does_not_grow_with_the_number_of_products`
  triples the row count and asserts the count does not move. The shared-catalog
  budget rose by one for the paginator's `COUNT`, which is flat.

---

## 10. Tests Added

414 → 568 tests (568 on PostgreSQL; 558 plus 10 skipped concurrency tests on
SQLite). 14 new files.

**PostgreSQL concurrency (10 tests, `@pytest.mark.concurrency`)**

| File | Covers |
|------|--------|
| `apps/accounting/tests/test_financial_concurrency.py` | Two threads invoicing one trade; two posting one trade; two finalizing one request; concurrent number allocation |
| `apps/accounts/tests/test_otp_concurrency.py` | Simultaneous verification of one code; parallel wrong guesses each costing an attempt; a burned code staying burned |
| `apps/inventory/tests/test_media_concurrency.py` | Two threads setting the cover; two simultaneous first uploads |
| `apps/inquiries/tests/test_inquiry_concurrency.py` | A double-submitted form recording one inquiry per seller |

Each races real threads through real connections, released together on a
`threading.Barrier` — without one, the first thread routinely finishes before the
second starts and the test passes against code with no concurrency control at all.

**Migration (9 tests)** — `test_migrations.py` drives the real migration graph
backwards to the pre-V2 schema, writes rows in every legacy visibility/status
shape, and migrates forward. Asserting on the migration function directly would
only have proved the constant is read.

**Security and authorization (44 tests)** — the eligibility matrix across
status × verification × plan × expiry on directory, marketplace, public search,
share links and purchase requests; historical accounting access; admin safety;
OTP hardening; media content validation.

**Regression (rest)** — staff workflows, freshness-aware filters, pagination,
document lifecycle.

### Tests that were verified to fail without their fix

A regression test that cannot fail proves nothing, so four were checked against
the original code:

- **AUD-001** — with the pre-fix ordering, both threads reach the `INSERT` and
  PostgreSQL rejects the second with `duplicate key value violates unique
  constraint "uniq_invoice_per_trade"`. Without the constraint, that is two
  invoices for one sale.
- **AUD-014** — restoring `COLLEAGUES_BECOME_VISIBLE = True` fails
  `test_only_previously_public_items_stay_published[colleagues-False]`.
- **AUD-017** — restoring read-check-write records **4** attempts for 5 parallel
  wrong guesses.
- **AUD-020** — removing the lot lock makes two simultaneous uploads violate
  `uniq_primary_media_per_lot`.

---

## 11. Test Results

Actual output, commit `67f0ff1`. Full logs: `/opt/cursor/artifacts/validation_sqlite.log`,
`/opt/cursor/artifacts/validation_postgres.log`.

```text
$ python manage.py check
System check identified no issues (0 silenced).

$ python manage.py makemigrations --check
No changes detected

$ ./scripts/check_fresh_migrate.sh
  Applying reservations.0002_delete_reservation... OK
  Applying sessions.0001_initial... OK
fresh migrate OK -> /tmp/sanga-fresh-8IU4GB.sqlite3

$ ruff check .
All checks passed!

$ pytest                                    # SQLite, fast lane
558 passed, 10 skipped, 164 warnings in 31.48s
```

```text
psql (PostgreSQL) 16.14

$ ./scripts/run_pg_tests.sh
568 passed, 164 warnings in 45.13s

$ ./scripts/run_pg_tests.sh -m concurrency -v
10 passed, 558 deselected, 1 warning in 5.68s

$ DJANGO_DATABASE=postgres python manage.py migrate      # empty database
  Applying pricing.0003_price_freshness_and_special_sale... OK
  Applying reservations.0002_delete_reservation... OK
  Applying sessions.0001_initial... OK

$ python manage.py check --deploy --fail-level WARNING   # production settings
System check identified no issues (0 silenced).
```

The 10 SQLite skips are the concurrency tests, skipped by design: SQLite ignores
`select_for_update` and serializes writers, so they would pass there without
proving anything.

**Production fail-closed, verified by hand:**

```text
SMS_PROVIDER=console (no escape hatch)
  ImproperlyConfigured: SMS_PROVIDER='console' does not send messages, so no user
  could log in and every OTP would be written to the application log. …

SMS_PROVIDER=kavenegarr (typo)
  ImproperlyConfigured: SMS_PROVIDER='kavenegarr' is not a provider SANGA knows.
  Choose one of: console, null.

DJANGO_ALLOWED_HOSTS unset (development .env still in place)
  ImproperlyConfigured: DJANGO_ALLOWED_HOSTS must name the real hostnames in
  production, not just ['0.0.0.0', '127.0.0.1', '::1', '[::1]', 'localhost'].

DJANGO_ALLOWED_HOSTS='*'
  ImproperlyConfigured: DJANGO_ALLOWED_HOSTS must not be '*' in production.
```

**Security headers, observed on a real response:**

```text
Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'
  'unsafe-inline' https://fonts.googleapis.com; font-src 'self'
  https://fonts.gstatic.com data:; img-src 'self' data: blob:; media-src 'self'
  blob:; connect-src 'self'; frame-ancestors 'none'; form-action 'self';
  base-uri 'self'; object-src 'none'
Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(), usb=()
X-Frame-Options: DENY
X-Content-Type-Options: nosniff

uses unpkg CDN: False        self-hosted vendor js: True        inline onclick: False
```

**Public flow, exercised end to end:**

```text
product detail     GET  -> 200
share link         GET  -> 200
shared catalog     GET  -> 200
public search      GET  -> 200
inquiry_start      POST -> 302 /inquiry/
review             GET  -> 200; product listed: True
review qty         POST -> 302 /inquiry/identify/
identify           GET  -> 200
direct POST bypass POST -> 405 (refused)
```

---

## 12. Remaining Risks

1. **The container was never run.** Docker is unavailable in this environment, so
   the `Dockerfile`, `entrypoint.sh` and `docker-compose.prod.yml` were reviewed
   as build files, not executed. **Build the image and run the stack in staging
   before relying on any of it** — particularly the build-time `collectstatic`
   and the `migrate`-before-`web` ordering.

2. **No real SMS gateway exists.** The registry is ready and production refuses
   to start without a delivering provider, but no adapter has been written or
   tested against a live Iranian gateway. This blocks launch on its own.

3. **The V1→V2 upgrade was tested on synthetic data.** The migration tests drive
   the real graph with representative legacy rows. A rehearsal against a copy of
   the production V1 database is still required, and the backup taken before it is
   the only record of the old `visibility` column once `inventory.0006` runs.

4. **No browser or device testing.** RTL rendering, the new pager on a phone, and
   the reworked public inquiry flow were verified through the Django test client
   and by inspecting rendered HTML. Keyboard order, focus, contrast, screen-reader
   behaviour and slow-network HTMX are unverified.

5. **CSP is enforced, not report-only.** It was checked against the pages this
   environment could render. Turn `CSP_REPORT_ONLY=true` on first in staging and
   watch for violations; `CSP_IMG_SRC` and `CSP_MEDIA_SRC` must name the object
   storage origin or product images will be blocked.

6. **Branch protection is not applied.** The CI workflow exists and runs; making
   it required on `master` is a repository setting.

7. **No load testing.** Pagination and the query budgets bound the work per
   request, but no throughput figure has been measured.

8. **Colleague direct sales are single-line.** A seller invoicing a colleague for
   several different stones must record several sales. This is a consequence of
   the 1:1 Trade↔Invoice relation and may need revisiting if it proves awkward in
   practice.

---

## 13. Production Readiness

| Category | Before | After | Assessment |
|----------|-------:|------:|------------|
| Functionality | 6/10 | **8/10** | Contradictory paths are gone: one sale workflow, one inquiry pipeline, no unreachable products. Colleague direct sales are single-line by design |
| Reliability | 5/10 | **8/10** | All-or-nothing submissions, idempotent finalization and invoicing, deterministic lock ordering, `on_commit` side effects. Untested under load |
| Security | 6/10 | **8/10** | Fail-closed production, verified uploads, CSP with self-hosted JS, locked OTP, admin cannot bypass domain rules. No live penetration test |
| Data integrity | 5/10 | **9/10** | Every invariant that mattered is a database constraint, not a lookup: invoice-per-trade, entry-per-trade-per-business, inquiry-per-submission-per-seller, one cover per item |
| Performance | 6/10 | **8/10** | Pagination everywhere, SQL-composed catalogs, O(1) numbering, budgets that fail on regression. No load figures |
| UX/UI | 6/10 | **7/10** | UI now reflects backend policy; one obvious path per intention; pager preserves state. No device or accessibility testing |
| Maintainability | 7/10 | **9/10** | Predicates named and paired with their SQL, one pagination helper, one media validator, dead dependency and legacy admin removed |
| Test coverage | 6/10 | **9/10** | 568 tests; the highest-risk races are covered on the production engine, and four were verified to fail without their fix |
| Deployment readiness | 3/10 | **6/10** | Real production image, CI gate, pinned dependencies, config that fails closed. Held back by the unrun container, the missing SMS gateway and no staging rehearsal |

---

## 14. Final Recommendation

## READY FOR CONTROLLED STAGING / PILOT

Every P1 finding is fixed, and the ones that mattered most are now enforced by
the database rather than by a check that concurrency can walk past. A sale cannot
be documented twice, cannot move one party's books and not the other's, and
cannot exist as an invoice with no matching balance. A suspended tenant cannot
write but does not lose its history. A public inquiry cannot be recorded against
an unverified phone, and cannot be duplicated by a retry. Production refuses to
start on a configuration that would silently log every login code.

It is not ready for general availability, for three reasons that no amount of
further code review can settle:

1. **No SMS gateway exists.** Production correctly refuses to start without one.
   Writing and testing that adapter against a live Iranian provider is the single
   largest remaining piece of work, and nothing can launch without it.

2. **The container has never been run.** Docker was unavailable here. A
   deployment path that has only been read is a hypothesis, and the specific
   things to prove are the build-time `collectstatic` and that `migrate`
   completes before any `web` replica starts.

3. **The V1 upgrade has not been rehearsed on real data.** The migration tests
   are thorough on synthetic rows, and `docs/v2-migration-strategy.md` §5 is now
   privacy-preserving, but a production dataset always has shapes a fixture does
   not.

The recommended sequence: deploy to staging from a clean environment, restore a
copy of the production V1 database and run the upgrade against it, verify that no
previously colleague-only product became publicly visible, integrate and test the
SMS gateway, run `CSP_REPORT_ONLY=true` for a week, do a browser and device pass
on the RTL interface, and test a backup **restore** rather than a backup. Then
pilot with a handful of businesses that know they are first.

The one thing to carry into that pilot: the invariants in this codebase are now
written down in two places — as constraints in the database and as tests that
have been shown to fail without them. When something surprising happens in
staging, those are the first places to look, and the second place is
`docs/`, which was updated in the same commits as the code it describes.
