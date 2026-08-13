# SANGA V2 — Remediation Plan

**Repository:** `mohammad5115m/SANGA`  
**Branch:** `cursor/sanga-v2-refactor-c477`  
**Source:** `PRODUCT_AUDIT_REPORT.md`

This plan intentionally avoids broad architectural rewrite. The current Django modular-monolith, service/selector boundaries, tenant model, shared item eligibility policy, invoice snapshots, and immutable-ledger design should be retained and hardened.

The order below is important: financial and authorization invariants must be fixed before UX cleanup or performance work, otherwise later work will be built on inconsistent rules.

---

# Phase 1 — Critical fixes

No confirmed P0 issue was found in the static audit. Before production, run the mandatory PostgreSQL concurrency/security/migration validation described in Phase 5; any P0 discovered there supersedes the remaining plan.

---

# Phase 2 — High-risk fixes

## Task 2.1 — Establish one authoritative commercial-sale workflow

- **Issues:** AUD-002, AUD-003, AUD-011
- **Files/modules affected:**
  - `apps/trading/models.py`
  - `apps/trading/services.py`
  - `apps/trading/views.py`
  - `apps/invoicing/services.py`
  - `apps/invoicing/views.py`
  - `apps/accounting/services.py`
  - `apps/accounting/models.py`
  - `apps/reporting/reports.py`
  - templates for direct sale / invoice / trade
- **Proposed solution:**
  1. Make `Trade` the only authoritative representation of a finalized sale, including manual/phone sales.
  2. Replace the colleague-side `create_manual_invoice()` UI path with a direct-sale workflow: enter buyer/item/quantity/final unit price → create Trade → post financial entries → create invoice.
  3. Keep a simple invoice-only path only for walk-in/customer documentation if the product explicitly needs it, or make that path create a customer Trade as well for consistent reports.
  4. Decide the buyer-side accounting invariant. Recommended: for Business-to-Business sales, atomically post seller `SALE` and buyer `PURCHASE` entries. Use a deterministic Business-row lock ordering (e.g. by UUID string) to avoid deadlocks.
  5. Keep stock non-authoritative and do not decrement it automatically.
  6. Ensure finalized sale → invoice creation does not depend on the acting salesperson having invoice-management capability. Invoice generation is a consequence of the sale, not a second user-authored commercial event. If only some users may “issue” invoices, create a draft system-generated invoice that authorized staff can issue later.
- **Dependencies:** Must define buyer-side ledger semantics before report changes.
- **Risk of changing it:** High; touches financial history and core workflows.
- **Tests required:**
  - request-driven sale creates exactly one Trade;
  - direct/phone colleague sale creates exactly one Trade;
  - seller balance increases exactly once;
  - buyer balance/purchase decreases/increases according to defined convention exactly once;
  - retry/double-click does not duplicate either side;
  - invoice creation/printing never creates a second ledger effect;
  - staff can complete every workflow their role exposes;
  - customer sale creates no Business-counterparty ledger entry;
  - reports include all finalized sales consistently.
- **Verification steps:**
  - run PostgreSQL concurrency tests;
  - manually finalize request-driven and direct colleague sales;
  - compare both parties' statements and reports;
  - retry finalize endpoints and confirm no duplicate rows.

## Task 2.2 — Enforce one invoice per Trade at the database level

- **Issue:** AUD-001
- **Files/modules affected:**
  - `apps/invoicing/models.py`
  - `apps/invoicing/services.py`
  - new migration
  - invoicing/accounting tests
- **Proposed solution:**
  - Make `SalesInvoice.trade` `OneToOneField` if every Trade can have at most one invoice, or add a conditional unique constraint for non-null Trade.
  - Acquire seller lock before checking for existing invoice, or re-check after lock.
  - On `IntegrityError`, fetch and return the winner invoice.
  - Preserve `(seller_business, number)` uniqueness.
- **Dependencies:** Can be done independently but should land before broader sale-flow changes.
- **Risk of changing it:** Medium; migration must detect any pre-existing duplicate Trade invoices.
- **Tests required:**
  - two concurrent creators for same Trade return same invoice;
  - no duplicate invoice rows;
  - sequential numbering remains valid.
- **Verification steps:** PostgreSQL `TransactionTestCase`/threaded test; inspect unique constraint in database.

## Task 2.3 — Centralize Business operational/network eligibility

- **Issues:** AUD-004, AUD-005, AUD-006
- **Files/modules affected:**
  - `apps/businesses/entitlements.py`
  - `apps/businesses/selectors.py`
  - `apps/businesses/middleware.py`
  - `apps/businesses/decorators.py`
  - `apps/businesses/directory.py`
  - `apps/inventory/policy.py`
  - public storefront resolver
  - marketplace and trading services
- **Proposed solution:**
  Define explicit policies instead of scattered checks:
  - `business_can_use_app(business)` — controls tenant session access;
  - `business_is_network_eligible(business)` — active + verification policy + subscription/current-plan policy;
  - `business_can_sell(business)` — seller plan/entitlement + operational/network eligibility;
  - `business_can_browse(business)` — browse entitlement/policy.

  Middleware/decorators should block suspended tenants centrally. Buyer-facing selectors should use `business_is_network_eligible/business_can_sell`, not just `status=ACTIVE`. Decide whether expired sellers retain private read-only access to historical records while being removed from public/network discovery.
- **Dependencies:** Product decision for verification eligibility should be explicit; recommended: only `VERIFIED` participates in shared network.
- **Risk of changing it:** Medium; can unexpectedly hide existing products if data is not verified.
- **Tests required:** matrix covering ACTIVE/SUSPENDED × VERIFIED/PENDING/REJECTED × SELLER/BROWSE × current/expired across login, directory, marketplace, public search, catalog, share URL, purchase request, and private history.
- **Verification steps:** Seed businesses in every state and manually test each surface.

## Task 2.4 — Separate historical accounting identity from live directory eligibility

- **Issue:** AUD-007
- **Files/modules affected:**
  - `apps/accounting/views.py`
  - `apps/accounting/selectors.py`
  - possibly `apps/businesses/directory.py`
- **Proposed solution:**
  - Create an accounting-specific `get_counterparty_for_business()` resolver.
  - It should authorize based on existing ledger/invoice/trade relationship, not current network visibility.
  - Suspended/unverified counterparties with historical balances remain readable.
  - Allow settlement/payment entries against an existing historical account even if counterparty is no longer discoverable, unless Platform Admin explicitly blocks financial settlement.
- **Dependencies:** Business operational policy from Task 2.3.
- **Risk of changing it:** Medium; must not broaden access to unrelated Businesses.
- **Tests required:**
  - unrelated Business remains inaccessible;
  - suspended historical debtor remains accessible from statement;
  - settlement entry works;
  - directory still hides non-network-eligible counterparty.
- **Verification steps:** create ledger → suspend counterparty → open/print statement and settle.

## Task 2.5 — Fix explicit empty-permission behavior

- **Issue:** AUD-008
- **Files/modules affected:**
  - `apps/businesses/models.py`
  - team/provisioning services
  - optional migration if initialization semantics need a nullable/sentinel field
- **Proposed solution:**
  - Apply role defaults only on initial object creation when permissions were not explicitly provided.
  - Preserve `[]` as a legitimate “no capabilities” value.
  - Keep owner bypass only if it is a deliberate product invariant; otherwise allow explicit restrictions for owner too.
- **Dependencies:** None.
- **Risk of changing it:** Low/medium; some existing rows may rely on blank list meaning defaults.
- **Tests required:** create staff with omitted permissions → defaults; create/update staff with `[]` → remains empty; suspended member remains no-access.
- **Verification steps:** edit member in admin/team UI and re-read effective capability set.

## Task 2.6 — Unify all public customer inquiries behind one OTP-backed workflow

- **Issues:** AUD-009, AUD-031
- **Files/modules affected:**
  - `apps/catalog/views_public.py`
  - `apps/catalog/views_inquiry.py`
  - `apps/catalog/cart.py`
  - `apps/inquiries/services.py`
  - product/shared-catalog templates
- **Proposed solution:**
  - Remove direct name/phone inquiry creation from product detail/shared catalog pages.
  - Product CTA should add/select the product and request quantity, then proceed to identity + customer OTP.
  - Shared catalog should allow selection of one or more catalog products with quantity before submission.
  - Keep “general question” only as a distinct feature if required, with explicit semantics and verification policy.
- **Dependencies:** Task 2.7 idempotent submission should be designed simultaneously.
- **Risk of changing it:** Medium UX change.
- **Tests required:** all entry points end in Customer OTP; OTP never creates User; quantity/items persist; expired/unavailable product cannot be submitted after OTP.
- **Verification steps:** public product, public search, shared catalog, multi-seller cart manual smoke tests.

## Task 2.7 — Make multi-seller inquiry submission atomic and idempotent

- **Issue:** AUD-010
- **Files/modules affected:**
  - `apps/catalog/views_inquiry.py`
  - `apps/inquiries/models.py`
  - `apps/inquiries/services.py`
  - migration
- **Proposed solution:**
  - Introduce a `submission_id` UUID generated before OTP and persisted in session/pending state.
  - Add uniqueness such as `(submission_id, business)` on Inquiry.
  - Wrap all per-seller Inquiry creation in one outer `transaction.atomic()` when feasible.
  - Notifications should execute after commit or be safe/idempotent themselves.
  - Retry should return the existing Inquiry set.
- **Dependencies:** Task 2.6.
- **Risk of changing it:** Medium; migration/new lifecycle.
- **Tests required:** failure on second seller rolls back all DB inquiry writes, or retry returns existing rows without duplicates; concurrent duplicate submission; notification failure behavior.
- **Verification steps:** inject exception on seller N and retry.

## Task 2.8 — Make stock/price search semantics freshness-aware

- **Issue:** AUD-012
- **Files/modules affected:**
  - `apps/inventory/filters.py`
  - `apps/inventory/models.py`
  - `apps/pricing/models.py`
  - pricing selectors/services
- **Proposed solution:**
  - Add reusable queryset predicates/annotations for “current numeric B2B/B2C price” and “current exact stock”.
  - Expired price should behave as inquiry in price filters/sort.
  - Expired exact/unlimited stock should behave as inquiry in stock/min-quantity filters.
  - Keep public/B2B price isolation in the query itself.
- **Dependencies:** None, but should land before pagination because query behavior will change.
- **Risk of changing it:** Medium; may alter result counts materially.
- **Tests required:** fresh vs expired B2B/B2C prices; special expiry; stock expiry; min quantity; sorting; combined filters.
- **Verification steps:** create controlled stale/fresh fixtures and compare displayed state to query inclusion.

## Task 2.9 — Correct V1→V2 visibility migration before real data upgrade

- **Issue:** AUD-014
- **Files/modules affected:**
  - `apps/inventory/migrations/0005_backfill_item_lifecycle.py` if not yet deployed anywhere;
  - otherwise a new corrective migration/management command and runbook.
- **Proposed solution:**
  - Conservative mapping: old `public → is_visible=True`; old `colleagues/private → False`.
  - If the migration has already run against production-like data, do not rewrite historical migration; create an explicit corrective migration using retained audit/source data if available, or a seller/admin review workflow.
- **Dependencies:** Confirm whether any real DB has run migration 0005.
- **Risk of changing it:** High if already deployed; low if branch is not deployed.
- **Tests required:** migration tests with each legacy visibility/status combination.
- **Verification steps:** migrate fixture DB from pre-V2 state and inspect anonymous/public visibility.

## Task 2.10 — Fail closed for production SMS configuration

- **Issue:** AUD-015
- **Files/modules affected:**
  - `config/settings/production.py`
  - `apps/accounts/sms.py`
  - `.env.example`
  - deployment docs
- **Proposed solution:**
  - Production startup must reject console/unknown/missing SMS provider.
  - Real provider credentials must be required through environment variables.
  - Never log OTP plaintext in production.
  - Add a provider health/config check.
- **Dependencies:** Select real production SMS provider before launch.
- **Risk of changing it:** Low; deployment may intentionally fail until credentials are set.
- **Tests required:** production settings fail for console/unknown; valid provider loads; DEBUG console still works.
- **Verification steps:** run `manage.py check --deploy` with safe/unsafe env combinations.

---

# Phase 3 — Reliability and UX

## Task 3.1 — Persist stock validity from confirmation form

- **Issue:** AUD-016
- **Files/modules affected:** `apps/inventory/views.py`, `apps/inventory/services.py`
- **Proposed solution:** Add `stock_valid_for_days` to `confirm_item_stock()` and persist it with the confirmation timestamp in the same transaction.
- **Dependencies:** None.
- **Risk:** Low.
- **Tests:** HTTP form changes value; expiry recomputes correctly.
- **Verification:** Submit 30-day confirmation, reload, inspect expiry.

## Task 3.2 — Harden OTP concurrency and abuse controls

- **Issue:** AUD-017
- **Files/modules affected:** `apps/accounts/services.py`, OTP models, cache/rate-limit helpers.
- **Proposed solution:**
  - Serialize verification/use with row lock or conditional atomic update.
  - Increment attempts atomically.
  - Add per-phone cooldown, per-IP window, and global/provider send limits.
  - Keep unknown/known phone behavior enumeration-safe.
- **Dependencies:** Redis can be used for rate limiting if already production-required, but correctness of one-time use should remain DB-backed.
- **Risk:** Medium; auth availability.
- **Tests:** concurrent verification, concurrent request burst, expiry, maximum attempts, enumeration parity.
- **Verification:** PostgreSQL + Redis integration tests.

## Task 3.3 — Validate real media content

- **Issue:** AUD-018
- **Files/modules affected:** `apps/inventory/services.py`, media utilities, dependencies if a video probe is added.
- **Proposed solution:** Verify image bytes through Pillow; use a lightweight safe video container probe for supported formats. Reject malformed content before final storage where practical.
- **Dependencies:** Decide video validation tool/library.
- **Risk:** Medium; some legitimate unusual files may be rejected.
- **Tests:** renamed text/HTML/invalid binary, malformed image, valid JPG/PNG/WebP/MP4/WebM, oversize.
- **Verification:** controlled upload corpus.

## Task 3.4 — Clean object storage when media is deleted

- **Issue:** AUD-019
- **Files/modules affected:** inventory media deletion service, storage layer.
- **Proposed solution:** Capture file/thumbnail names, delete DB row transactionally, schedule storage deletion with `transaction.on_commit`. Add an orphan-media audit/cleanup command.
- **Dependencies:** Task 3.3 optional.
- **Risk:** Medium; careless cleanup can remove shared files, so ensure each media record owns its object key.
- **Tests:** mocked storage; rollback does not delete; committed deletion removes file+thumbnail.
- **Verification:** local and S3-compatible test bucket.

## Task 3.5 — Enforce one primary image

- **Issue:** AUD-020
- **Files/modules affected:** `apps/inventory/models.py`, services, migration.
- **Proposed solution:** Conditional unique constraint for primary per lot; lock lot when changing; ensure primary must be image if that is invariant.
- **Dependencies:** None.
- **Risk:** Low/medium migration if duplicates already exist.
- **Tests:** concurrent primary selection and upload; deletion promotes exactly one image.
- **Verification:** PostgreSQL concurrency test.

## Task 3.6 — Hide drafts from buyers

- **Issue:** AUD-021
- **Files/modules affected:** `apps/invoicing/selectors.py`, invoice views/tests.
- **Proposed solution:** Seller sees all own invoices. Buyer side filters to ISSUED (and CANCELLED only if policy says cancelled documents remain visible). Drafts remain internal.
- **Dependencies:** Phase 2 sale/invoice workflow decisions.
- **Risk:** Low.
- **Tests:** buyer cannot list/get draft; seller can; issued becomes visible.
- **Verification:** two Business sessions.

## Task 3.7 — Make plan/entitlement-aware navigation

- **Issue:** AUD-025
- **Files/modules affected:** context processor, app shell, dashboard/more templates.
- **Proposed solution:** Expose combined `can_*` UI policy values or check both `capabilities` and `entitlements`. Remove seller-only central Add button for browse/expired Business and provide a clear plan-state banner.
- **Dependencies:** Central eligibility functions from Task 2.3.
- **Risk:** Low.
- **Tests:** seller/browse/expired/suspended navigation matrix.
- **Verification:** manual mobile/desktop navigation.

## Task 3.8 — Add real pagination to discovery and operational lists

- **Issue:** AUD-013
- **Files/modules affected:** public search, marketplace, owner inventory, invoices, reports/trading/leads as appropriate.
- **Proposed solution:** Django `Paginator` initially; preserve validated filters and sort query strings; handle invalid/out-of-range page cleanly; consider keyset pagination later only if needed.
- **Dependencies:** Freshness-aware filters Task 2.8 first.
- **Risk:** Medium UX/template change.
- **Tests:** page boundaries, combined filters, changed filters while on later page, Unicode query, empty pages.
- **Verification:** seed >200 records and navigate on mobile/desktop.

## Task 3.9 — Clarify statement and invoice-report semantics

- **Issue:** AUD-032
- **Files/modules affected:** reporting/accounting selectors, templates.
- **Proposed solution:**
  - invoice monetary totals should clearly sum ISSUED only unless label explicitly includes drafts;
  - statement should show opening balance, filtered period movements, and closing balance, rather than implying last visible row's global running balance is derived only from visible rows.
- **Dependencies:** Phase 2 financial model decisions.
- **Risk:** Medium; report values change.
- **Tests:** filtered date/type fixtures, draft/cancelled invoices, opening/closing balances.
- **Verification:** reconcile manually with a known ledger fixture.

---

# Phase 4 — Performance and maintainability

## Task 4.1 — Replace invoice-number scan with an efficient sequence

- **Issue:** AUD-022
- **Files/modules affected:** invoicing models/services/migration.
- **Proposed solution:** Add a per-Business `next_invoice_number` counter or dedicated sequence row updated with `select_for_update/F()`. Keep formatted display number separate from numeric sequence.
- **Dependencies:** Invoice-per-Trade fix should already be complete.
- **Risk:** Medium migration.
- **Tests:** thousands of invoices, cancellation, concurrent allocations, no reuse.
- **Verification:** PostgreSQL load/concurrency test.

## Task 4.2 — Keep dynamic catalog evaluation in SQL

- **Issue:** AUD-026
- **Files/modules affected:** `apps/catalog/selectors.py`.
- **Proposed solution:** Avoid materializing rule IDs; combine querysets/Q predicates for rules, includes, excludes and eligibility. Make resolved catalog pageable if very large.
- **Dependencies:** Search filter semantics stabilized.
- **Risk:** Low/medium.
- **Tests:** manual/rule/hybrid include/exclude equivalence; query count; large dataset.
- **Verification:** inspect SQL/query count and response time with seeded dataset.

## Task 4.3 — Protect commercial history in Django Admin

- **Issues:** AUD-023, AUD-024, AUD-037
- **Files/modules affected:** invoicing/trading/businesses/legacy app admin files.
- **Proposed solution:**
  - disable hard delete for Business, Trade, issued/cancelled Invoice;
  - mark historical commercial fields read-only;
  - use suspend/archive operations;
  - remove Warehouse inline/admin from normal platform operations;
  - clearly label migration-only legacy models if admin exposure must remain.
- **Dependencies:** Business lifecycle policy from Phase 2.
- **Risk:** Low.
- **Tests:** admin permission/delete tests where practical.
- **Verification:** manual Django Admin review.

## Task 4.4 — Decide and test duplicate-product promotion behavior

- **Issue:** AUD-036
- **Files/modules affected:** `apps/inventory/services.py::duplicate_item`, UX copy/tests.
- **Proposed solution:** Either copy `special_amount/special_until` or intentionally reset and tell the seller. Prefer reset if promotion is time-sensitive, but make it explicit.
- **Dependencies:** None.
- **Risk:** Low.
- **Tests:** duplicate active special sale.
- **Verification:** seller UI.

## Task 4.5 — Remove unused runtime dependencies and legacy technical surfaces

- **Issues:** AUD-035, AUD-037
- **Files/modules affected:** requirements/settings/admin/legacy app configuration.
- **Proposed solution:** Verify DRF has no imports/routes before removing it. Remove Warehouse runtime/admin surface after migration safety is confirmed. Keep migration-only apps only as needed for historical dependency graph.
- **Dependencies:** Fresh migration test.
- **Risk:** Low/medium if hidden imports exist.
- **Tests:** import checks, full test suite, fresh migration.
- **Verification:** `pip check`, `manage.py check`, application startup.

---

# Phase 5 — Production hardening

## Task 5.1 — Add mandatory GitHub CI and protect master

- **Issue:** AUD-027
- **Files/modules affected:** `.github/workflows/*`, repository settings.
- **Proposed solution:** Required jobs:
  1. Python 3.12 install from pinned constraints;
  2. Ruff;
  3. `python manage.py check`;
  4. `python manage.py makemigrations --check`;
  5. fast pytest lane;
  6. PostgreSQL pytest/integration lane;
  7. fresh `migrate` from zero;
  8. optional V1 fixture→V2 migration job;
  9. `manage.py check --deploy` with production-like env.
  Protect `master` and require passing checks + review.
- **Dependencies:** Tests from prior phases.
- **Risk:** Low; may initially expose failures.
- **Tests required:** CI is the verification mechanism.
- **Verification:** intentionally failing PR must be blocked.

## Task 5.2 — Add PostgreSQL concurrency/integration suite

- **Issue:** AUD-028 and concurrency-related issues across the report.
- **Files/modules affected:** test infrastructure, `conftest.py`, CI.
- **Proposed solution:** Keep SQLite for fast unit tests if desired, but provide a production-DB marker/job. Use `TransactionTestCase`/pytest transaction tests and actual concurrent DB connections for locks/unique constraints.
- **Dependencies:** PostgreSQL CI service.
- **Risk:** Low.
- **Tests:** invoice uniqueness/numbering, ledger two-sided posting, sale double finalize, OTP, media primary, seat additions, migration constraints.
- **Verification:** tests must fail if locks/constraints are removed.

## Task 5.3 — Build a production container and deployment path

- **Issue:** AUD-029
- **Files/modules affected:** `Dockerfile`, compose/deploy manifests, requirements, docs.
- **Proposed solution:**
  - multi-stage/non-root image;
  - install production dependencies only;
  - Gunicorn entrypoint;
  - `collectstatic` strategy;
  - explicit migration release step, not every app replica racing migrations;
  - health/readiness probes;
  - Redis/Postgres not publicly exposed in production;
  - object-storage configuration;
  - backup and tested restore procedure;
  - structured logs + error monitoring hook.
- **Dependencies:** Real hosting choice.
- **Risk:** Medium operational change.
- **Tests:** build image, smoke app, health, static/media, migration in staging.
- **Verification:** deploy to staging from clean environment and restore a backup.

## Task 5.4 — Pin production dependencies and add vulnerability scanning

- **Issue:** AUD-034
- **Files/modules affected:** requirements/build process.
- **Proposed solution:** Generate reviewed pinned constraints/lock for production; keep source ranges separately if desired. Add Dependabot/Renovate or scheduled dependency review and a vulnerability scanner.
- **Dependencies:** None.
- **Risk:** Low.
- **Tests:** clean reproducible build.
- **Verification:** two builds from same commit resolve identical versions.

## Task 5.5 — Self-host/pin frontend scripts and add CSP

- **Issue:** AUD-030
- **Files/modules affected:** `templates/base.html`, static assets, production security settings/middleware.
- **Proposed solution:** Prefer self-hosted pinned Alpine/HTMX. Add CSP, `Referrer-Policy`, `Permissions-Policy` as appropriate and keep Django security headers/HSTS. Remove inline JS or use CSP nonces/hashes where necessary.
- **Dependencies:** Frontend smoke tests.
- **Risk:** Medium; CSP can break Alpine/HTMX/inline scripts if introduced abruptly.
- **Tests:** browser smoke, CSP report-only first, no console violations.
- **Verification:** security headers inspection and core flow browser run.

## Task 5.6 — Final launch validation

- **Issues:** All P1/P2 release blockers
- **Files/modules affected:** Whole application/deployment.
- **Proposed solution:** Complete a staging release rehearsal with a realistic dataset.
- **Dependencies:** All prior production-blocking tasks.
- **Risk:** None; validation only.
- **Required verification:** 
  - `python manage.py check`
  - `python manage.py check --deploy`
  - `python manage.py makemigrations --check`
  - full pytest on PostgreSQL
  - fresh `python manage.py migrate`
  - tested upgrade migration from V1-shaped fixture
  - backup + restore test
  - seller/browse-only/suspended/unverified role matrix
  - B2B purchase → accept → finalize → both ledgers → invoice
  - direct phone sale → Trade → ledger → invoice
  - public multi-product/multi-seller inquiry → OTP → idempotent submit
  - hidden/unavailable/deleted product across marketplace/public/catalog/share URL
  - expired stock/price across display **and filtering**
  - media upload/delete and S3 behavior
  - mobile/desktop/browser-back/slow-network smoke tests
  - basic accessibility audit
  - security-header/session/CSRF/OTP abuse smoke test

---

# Safest Implementation Order

1. **AUD-001 invoice uniqueness** — establish DB safety first.
2. **AUD-002/AUD-003/AUD-011 financial event model** — one Trade-driven sale flow; resolve both-side accounting and automatic invoice consequence.
3. **AUD-004/AUD-005/AUD-006 centralized Business eligibility** — prevent invalid tenants/sellers from participating.
4. **AUD-007 historical accounting access** — avoid breaking debt settlement when network state changes.
5. **AUD-008 permission initialization** — close RBAC surprise.
6. **AUD-009/AUD-010/AUD-031 public inquiry unification/idempotency**.
7. **AUD-012 search freshness semantics**.
8. **AUD-014 migration privacy correction** before any real upgrade.
9. **AUD-015 production SMS fail-closed**.
10. Remaining Phase 3 reliability/UX fixes.
11. Performance/admin cleanup.
12. CI/PostgreSQL/deployment hardening and full staging release rehearsal.

Do not begin cosmetic redesign or broad architecture changes before items 1–9 are complete and protected by regression tests.
