# SANGA V2 — Product & Engineering Audit Report

**Repository:** `mohammad5115m/SANGA`  
**Branch audited:** `cursor/sanga-v2-refactor-c477`  
**Audit date:** 2026-08-13  
**Audit mode:** Static repository audit through the GitHub connector, including models, services, selectors, views, forms, templates, migrations, tests, settings, Docker/deployment files, and product/architecture documentation.

> Runtime limitation: the audit environment could not clone GitHub (`Could not resolve host: github.com`), and the branch has no CI/status checks attached to its current head. Therefore this report does **not** claim that `pytest`, `manage.py check`, fresh migrations, browser smoke tests, or PostgreSQL concurrency tests were executed by the auditor. Findings marked **Confirmed** are verified directly from code paths; findings that require runtime behavior are labeled accordingly.

---

## 1. Executive Summary

The V2 refactor is a substantial improvement over the previous product direction. The codebase now has a much clearer domain model: product discovery is centralized, availability is separated from stock freshness, B2B/B2C prices are isolated, purchase-request acceptance is separated from sale finalization, invoice snapshots preserve history, the seller ledger is immutable, customer OTP is separated from platform-user OTP, and dynamic catalog eligibility is centralized.

The architecture is generally appropriate for this product: a Django modular monolith with service/selector boundaries, PostgreSQL as the production database, server-rendered RTL pages, and explicit tenant scoping. A rewrite is **not** recommended.

However, the application is **not production-ready yet**. The most serious remaining risks are concentrated in four areas:

1. **Financial correctness:** a manually issued colleague invoice can bypass Trade/ledger posting; buyer-side purchase accounting is not posted; invoice creation for one Trade is race-prone; accounting access is incorrectly coupled to active-directory eligibility.
2. **Authorization / account lifecycle:** suspended Businesses can still use many authenticated application surfaces, explicit empty member permissions are silently replaced with role defaults, and network eligibility ignores verification and subscription/plan state.
3. **Public inquiry integrity:** product/catalog inquiry routes bypass the intended customer OTP workflow, and multi-seller submission can partially commit and duplicate requests on retry.
4. **Production hardening:** the default SMS provider can fall back to console logging, there is no CI/protected release gate, default tests are SQLite-only despite PostgreSQL locking/constraint logic, and the provided container runs Django's development server with development dependencies.

### Issue count

- **P0 — Critical:** 0 confirmed
- **P1 — High:** 15
- **P2 — Medium:** 17
- **P3 — Low:** 5
- **Total:** 37

No confirmed P0 issue was found in the static audit. This should not be interpreted as proof that no P0 can exist; live penetration testing, browser testing, fresh-database migration testing, and PostgreSQL concurrency testing were not available in this audit environment.

### Production recommendation

**Do not expose SANGA to real business users yet.** Resolve all P1 issues, add a PostgreSQL-backed CI gate, verify migrations on both a fresh database and a realistic V1→V2 dataset, and perform a production configuration/security smoke test before launch.

---

## 2. Architecture Overview

### Major modules

- `apps/accounts` — custom phone-based User, login OTP, customer OTP purpose separation.
- `apps/businesses` — tenant (`Business`), memberships, roles/capabilities, plan entitlements, colleague directory, dashboard.
- `apps/inventory` — Product Definition, sellable `InventoryLot`, lifecycle, stock freshness, media, shared product eligibility policy.
- `apps/pricing` — B2B/B2C price tiers, fixed/inquiry mode, validity/freshness, special price.
- `apps/marketplace` — authenticated colleague discovery using the inventory eligibility policy.
- `apps/catalog` — anonymous public discovery, per-product share URLs, manual/rule/hybrid catalogs, public selection cart.
- `apps/inquiries` — `CustomerLead`, `Inquiry`, `InquiryItem`, seller lead/inquiry workflow.
- `apps/trading` — product-bound `PurchaseRequest`, seller response, finalized `Trade`, direct-sale service.
- `apps/invoicing` — `SalesInvoice`, immutable-ish line snapshots, print view, manual invoice creation.
- `apps/accounting` — immutable `LedgerEntry`, seller-side sale posting, manual receipt/payment/adjustments, reversals, statements.
- `apps/reporting` — sales, invoice, balance, stock/price confirmation reports.
- `apps/notifications` — in-app per-user notifications.
- Legacy apps/models remain where required by migration history (`contacts`, old purchase-request/offer concepts, reservations/matching stubs).

### Main entity relationships

`Business` is the tenant. `BusinessMembership` connects a platform-created `User` to the tenant. `Product` stores reusable stone identity; `InventoryLot` represents one sellable product/item. Prices and media belong to the sellable item. A B2B purchase request connects buyer Business, seller Business, and an existing item. Finalization creates a historical Trade snapshot. A SalesInvoice may reference a Trade and contains immutable line snapshots. Seller-side financial impact is represented by immutable ledger entries keyed to the counterparty Business. Public customers are represented by CustomerLead/Inquiry records, not platform Users.

### Authentication and authorization

- Platform staff/users log in with OTP against an already provisioned User.
- Unknown phones do not create Users.
- `CurrentBusinessMiddleware` resolves an active membership and attaches `request.business`/`request.membership`.
- Capabilities live on memberships; plan entitlements live on Business and are intended to be checked independently.
- Public B2C paths do not require login.

### Key workflows

- Seller product: create → configure stock/prices/media → publish → update freshness → mark unavailable/available → share/catalog → delete/archive.
- B2B: colleague search → product-bound purchase request → seller accept/reject → separate finalize-sale action → Trade → seller ledger → invoice.
- Public: anonymous search → product selection → customer identity/OTP → seller-grouped inquiries.
- Accounting: finalized colleague sale creates receivable; manual receive/pay/adjust entries; corrections via reversal.
- Catalog: manual/rule/hybrid selection is intersected with current public eligibility.

### External/configuration dependencies

- PostgreSQL target production DB; SQLite is forced for default pytest.
- Redis/Celery exist in configuration.
- S3-compatible storage can be enabled.
- Alpine.js and HTMX are loaded from public CDNs in the base template.
- Production OTP requires an SMS provider, but configuration currently permits console fallback.

---

## 3. Critical Issues — P0

No P0 issue was confirmed by static inspection.

Before launch, this conclusion must be re-evaluated after PostgreSQL concurrency tests, fresh/upgrade migration tests, and live authorization/security smoke testing.

---

## 4. High Priority Issues — P1

### AUD-001 — One Trade can receive duplicate invoices under concurrency

- **Severity:** P1
- **Confidence:** Confirmed
- **Category:** Concurrency / financial data integrity
- **Location:** `apps/invoicing/services.py::create_invoice_for_trade`, `apps/invoicing/models.py::SalesInvoice.trade`
- **Description:** The service checks for an existing invoice before locking the seller Business. After acquiring the lock it does not re-check. `SalesInvoice.trade` is not unique/OneToOne.
- **Trigger:** Two requests call `create_invoice_for_trade()` for the same Trade at nearly the same time.
- **Expected:** Exactly one invoice is created and both callers resolve to it.
- **Actual:** Both can observe no invoice; they then serialize invoice-number allocation but can still create two distinct invoices for the same Trade.
- **Root cause:** Lookup-based idempotency without a database uniqueness invariant and without a post-lock recheck.
- **Recommended fix:** Add a conditional unique constraint (or OneToOne relationship) for non-null `trade`; acquire the lock before the idempotency lookup or re-check after locking; catch the unique violation and return the existing invoice. Add a PostgreSQL concurrency regression test.

### AUD-002 — Manual colleague invoice bypasses Trade and ledger posting

- **Severity:** P1
- **Confidence:** Confirmed
- **Category:** Business logic / financial correctness
- **Location:** `apps/invoicing/views.py::invoice_create`, `apps/invoicing/services.py::create_manual_invoice`, `apps/trading/services.py::record_direct_sale`
- **Description:** The UI offers manual invoice creation for a colleague Business. `create_manual_invoice()` creates only the invoice and invoice lines. It never creates a Trade or ledger entry. A correct `record_direct_sale()` service exists and does post the ledger, but no equivalent user-facing direct-sale workflow is wired to the invoice form.
- **Trigger:** Seller chooses a colleague in the manual invoice page and issues a sales invoice.
- **Expected:** A colleague sale changes that colleague's account exactly once and remains linked to the commercial transaction.
- **Actual:** A valid-looking invoice can exist while the colleague's debt/balance remains unchanged.
- **Root cause:** Two separate UI paths represent the same business event, but only one path owns the financial side effect.
- **Recommended fix:** Make direct sale the authoritative UI workflow for manual/phone sales: create Trade → post ledger exactly once → create/link invoice atomically. Do not let a Business-counterparty sales invoice independently represent a sale. Preserve document-only invoice creation only if explicitly modeled and clearly distinguished.

### AUD-003 — Buyer-side PURCHASE accounting is never posted for V2 trades

- **Severity:** P1
- **Confidence:** Confirmed
- **Category:** Financial reporting / missing functionality
- **Location:** `apps/accounting/models.py`, `apps/accounting/services.py::post_trade_for_sale`, `apps/reporting/reports.py::money_movement`
- **Description:** `LedgerEntry.Type.PURCHASE` exists, the trade unique constraint is intentionally scoped by Business so both sides can hold entries, and reports aggregate purchases. The V2 finalization path posts only the seller's `SALE`; no buyer-side `PURCHASE` is created, and manual entry APIs intentionally disallow PURCHASE.
- **Trigger:** Business B buys from Business A through a finalized V2 purchase request.
- **Expected:** Seller sees a receivable/sale; buyer's own books can consistently show the corresponding purchase/payable and purchase totals.
- **Actual:** Seller books move, buyer books do not; V2 purchase totals can remain zero/legacy-only.
- **Root cause:** Financial finalization is implemented one-sided despite a two-sided schema/report model.
- **Recommended fix:** Decide and document the accounting invariant. If SANGA is expected to maintain both Businesses' ledgers, post `SALE` and `PURCHASE` atomically, with deterministic locking order and per-Business idempotency. If buyer-side books are intentionally out of scope, remove misleading PURCHASE/report behavior instead of presenting incomplete totals.

### AUD-004 — Suspending a Business does not suspend most authenticated application access

- **Severity:** P1
- **Confidence:** Confirmed
- **Category:** Authorization / account lifecycle
- **Location:** `apps/businesses/middleware.py`, `apps/businesses/selectors.py::memberships_for_user`, `apps/businesses/decorators.py`
- **Description:** Current Business resolution checks only membership status. `business_login_required` checks only authenticated User; capability checks check membership/capability. Business `status=SUSPENDED` is not a central access gate.
- **Trigger:** Platform Admin suspends a Business while its memberships remain active; an already provisioned user logs in/continues a session.
- **Expected:** The tenant is blocked from normal application usage except intentionally allowed suspension/account pages.
- **Actual:** Many owner-only surfaces (inventory, ledger, reports, invoices, settings) remain reachable; only some shared marketplace/service paths separately reject suspended businesses.
- **Root cause:** Business operational state is enforced inconsistently at individual domain boundaries instead of the request/tenant boundary.
- **Recommended fix:** Add a centralized operational-Business gate in middleware/decorator and a clear suspended/expired experience. Preserve explicitly allowed read-only/history access only by deliberate policy.

### AUD-005 — Published products survive seller-plan downgrade or subscription expiry in discovery

- **Severity:** P1
- **Confidence:** Confirmed
- **Category:** Authorization / product eligibility
- **Location:** `apps/inventory/policy.py::eligible_items`, `apps/businesses/entitlements.py`, `apps/businesses/tests/test_entitlements.py`
- **Description:** Buyer-facing product eligibility checks seller `status=ACTIVE` but does not check seller plan, `active_until`, or seller entitlement. Write services correctly block new publishing after downgrade/expiry, but already-published products remain discoverable.
- **Trigger:** Seller publishes products, then plan changes to browse-only or subscription expires.
- **Expected:** Seller-only commercial surfaces stop presenting those products while subscription/seller entitlement is invalid.
- **Actual:** Public/B2B users can still discover the products. Creating a B2B request can later fail because `create_purchase_request()` re-checks the seller entitlement, producing a broken journey.
- **Root cause:** Read eligibility and write entitlement policies are separate and inconsistent.
- **Recommended fix:** Introduce one `network/seller_eligible` predicate combining active status, current subscription, seller-plan entitlement, and verification policy; use it in `eligible_items`, directory/storefront resolution, and request creation.

### AUD-006 — Verification status is not enforced in colleague directory or marketplace/public eligibility

- **Severity:** P1
- **Confidence:** Confirmed
- **Category:** Authorization / trust policy
- **Location:** `apps/businesses/directory.py`, `apps/inventory/policy.py`, `apps/businesses/models.py`
- **Description:** `verification_status` exists as a distinct field, but colleague and seller discovery currently filter only `status=ACTIVE`.
- **Trigger:** Business is ACTIVE but PENDING/UNVERIFIED/REJECTED.
- **Expected:** According to the agreed product policy, only eligible verified Businesses participate in the shared directory/network.
- **Actual:** Such a Business can be discoverable and its published items can appear.
- **Root cause:** Verification was modeled but never added to the shared eligibility predicate.
- **Recommended fix:** Centralize and enforce network eligibility. Add tests for every status/verification combination on directory, marketplace, public search, catalog, share URL, and purchase request.

### AUD-007 — Financial history/access disappears when a counterparty is suspended

- **Severity:** P1
- **Confidence:** Confirmed
- **Category:** Financial integrity / authorization coupling
- **Location:** `apps/accounting/views.py::_colleague_or_404`, `apps/businesses/directory.py::get_colleague`, `apps/accounting/selectors.py`
- **Description:** Ledger balances are built from historical entries and can include any counterparty Business, but statement/add-entry/print routes resolve the counterparty through the active colleague directory.
- **Trigger:** A debtor/creditor Business is suspended after financial history exists.
- **Expected:** Existing statement/invoices remain readable, and an authorized business can record settlement/payment against historical debt as policy permits.
- **Actual:** Ledger index can still show the account, but opening its statement can return 404; new settlement entries can be blocked solely because the counterparty is no longer directory-active.
- **Root cause:** Network-discovery eligibility is reused for historical accounting identity.
- **Recommended fix:** Add a tenant-scoped accounting counterparty resolver that accepts historical/suspended counterparties referenced by this Business's records. Keep directory eligibility separate.

### AUD-008 — Explicitly removing all member permissions silently re-grants role defaults

- **Severity:** P1
- **Confidence:** Confirmed
- **Category:** Authorization / RBAC
- **Location:** `apps/businesses/models.py::BusinessMembership.save`
- **Description:** The model initializes defaults with an `if not self.permissions` style check. An intentional empty list is therefore indistinguishable from “not initialized.”
- **Trigger:** Platform Admin or team-management logic sets an active member's permissions to `[]` and saves.
- **Expected:** User has zero capabilities.
- **Actual:** Role defaults are silently repopulated, potentially granting create/edit/sale access that the admin tried to remove.
- **Root cause:** Truthiness is used as initialization state.
- **Recommended fix:** Use a sentinel/`None`-style initialization or initialize only when `_state.adding` and the field was not explicitly supplied. Add tests for explicit empty permissions for staff/manager roles.

### AUD-009 — Direct public product and shared-catalog inquiries bypass customer OTP

- **Severity:** P1
- **Confidence:** Confirmed
- **Category:** Authentication / public workflow
- **Location:** `apps/catalog/views_public.py::lot_detail`, `apps/catalog/views_public.py::shared_catalog`, customer OTP flow in `apps/catalog/views_inquiry.py`
- **Description:** The multi-product submission path uses customer OTP, but direct product and shared-catalog POST handlers call `create_inquiry()` immediately using name/phone.
- **Trigger:** Anonymous user submits the inquiry form on a product detail or shared catalog page.
- **Expected:** The public inquiry phone is verified through the customer OTP purpose before final submission, per the V2 workflow.
- **Actual:** Inquiry is stored with an unverified phone without OTP.
- **Root cause:** Multiple public inquiry entry points were not routed through one workflow/service boundary.
- **Recommended fix:** Route every customer inquiry that requires verified identity through one pending-submission → OTP → commit flow. Explicitly document any intentionally exempt low-friction “stock inquiry” path.

### AUD-010 — Multi-seller public inquiry can partially commit and duplicate on retry

- **Severity:** P1
- **Confidence:** Confirmed
- **Category:** Transaction integrity / idempotency
- **Location:** `apps/catalog/views_inquiry.py::_submit`, `apps/inquiries/services.py::create_inquiry`
- **Description:** A selected cart can contain products from multiple sellers. `_submit()` loops and creates one Inquiry per seller. Each service call is atomic independently, but there is no outer transaction or submission idempotency key.
- **Trigger:** Seller 1 inquiry succeeds; seller 2 inquiry fails (validation/database/notification exception). User retries.
- **Expected:** Either the submission completes consistently or a retry does not duplicate already-created seller inquiries.
- **Actual:** Earlier seller inquiries remain committed while the page reports failure; retry can duplicate those inquiries.
- **Root cause:** Cross-seller submission is a multi-write workflow without an all-or-nothing boundary or durable idempotency token.
- **Recommended fix:** Use an outer transaction when all writes are in the same DB and introduce a submission UUID/idempotency key with uniqueness per seller. Create notifications on commit or ensure their failure cannot leave contradictory state.

### AUD-011 — Default staff permissions make the product/sale workflow internally inconsistent

- **Severity:** P1
- **Confidence:** Confirmed
- **Category:** Authorization / workflow failure
- **Location:** `apps/businesses/permissions.py::ROLE_DEFAULTS`, `apps/inventory/views.py::_create_or_update_draft`, `apps/trading/services.py::finalize_sale`, `apps/invoicing/services.py::safe_create_invoice_for_trade`
- **Description:** Staff can `inventory.create` and `sale.finalize`, but cannot `prices.edit` or `invoice.manage`. The quick-add wizard always writes B2B/B2C prices, so staff can enter the wizard but fail when prices are persisted. In the create path, draft creation and price update are separate atomic service calls, so a failed price write can leave an orphan draft. Separately, a staff user can finalize a sale, but automatic invoice creation is silently swallowed because the user lacks invoice-manage permission; the Trade detail page provides no recovery/create-invoice action.
- **Trigger:** Default staff creates a product or finalizes a sale.
- **Expected:** Default role can complete every workflow its permissions/navigation expose, or be blocked before entering it.
- **Actual:** It can enter workflows that cannot complete cleanly; partial drafts or invoice-less finalized sales can result.
- **Root cause:** Role matrix and workflow prerequisites were designed independently; the wizard is not one atomic service-level use case.
- **Recommended fix:** Define workflow-level capability requirements. Either grant the appropriate price/invoice capability to sales staff or change the workflows so staff can create inquiry-priced products and request invoice issuance by authorized members. Wrap draft+price creation in one transaction/service.

### AUD-012 — Price/stock search filters use stale stored values instead of effective freshness-aware values

- **Severity:** P1
- **Confidence:** Confirmed
- **Category:** Search / business logic
- **Location:** `apps/inventory/filters.py`, `apps/inventory/freshness.py`, `apps/pricing/models.py`
- **Description:** Cards correctly degrade stale stock to `استعلام موجودی` and expired fixed prices to inquiry behavior, but filter/sort queries use stored `stock_mode`, `available_sqm`, and price `amount` without applying expiry/effective-mode logic.
- **Trigger:** Exact stock or fixed price expires; buyer filters by minimum quantity, stock mode, price range, or price sorting.
- **Expected:** An item displayed as “inquiry” must not satisfy a filter that claims a current numeric amount/quantity.
- **Actual:** Stale values can qualify and sort results even though the user cannot see/trust those numbers.
- **Root cause:** Presentation freshness logic and SQL filter semantics are separate.
- **Recommended fix:** Define reusable query predicates/annotations for effective current stock and current audience price, including special-price expiry. Add stale/fresh combination tests for public, B2B, and owner search.

### AUD-013 — Core product discovery has hard caps instead of pagination

- **Severity:** P1
- **Confidence:** Confirmed
- **Category:** Functionality / scalability / UX
- **Location:** `apps/catalog/views_public.py` (`MAX_CARDS=60`), `apps/marketplace/views.py` (`MAX_CARDS=80`), `apps/inventory/views.py` (`[:100]`), invoice/report lists also use slices
- **Description:** The main discovery surfaces slice querysets and expose no next page.
- **Trigger:** More than 60 public or 80 colleague products match a search (or >100 owner products).
- **Expected:** Every matching product remains reachable through pagination while preserving filters/sort.
- **Actual:** Products beyond the slice are inaccessible unless the user guesses narrower filters.
- **Root cause:** MVP display caps were used as a substitute for pagination.
- **Recommended fix:** Add Django pagination (or keyset pagination where justified), preserve query parameters, reset invalid pages when filters change, and test empty/later-page/sort combinations.

### AUD-014 — V1 colleague-only products are migrated to anonymous-public visibility

- **Severity:** P1
- **Confidence:** Confirmed
- **Category:** Migration / privacy
- **Location:** `apps/inventory/migrations/0005_backfill_item_lifecycle.py`
- **Description:** The migration explicitly states that old `visibility="colleagues"` meant B2B-only while new `is_visible=True` is public+B2B. It nevertheless sets `COLLEAGUES_BECOME_VISIBLE = True`.
- **Trigger:** Run V1→V2 migration on data containing colleague-only products.
- **Expected:** Migration does not widen audience without seller consent.
- **Actual:** Product existence, images/specifications and B2C-safe information become publicly discoverable.
- **Root cause:** A product simplification decision was encoded as an opt-out public migration rather than a conservative privacy migration.
- **Recommended fix:** Before real-data migration, map old `public→True`, old `colleagues/private→False`, then require sellers/admin to republish under the new policy. If existing deployed data has already migrated, provide an explicit corrective migration/runbook.

### AUD-015 — Production can silently use the console SMS provider and log OTP content

- **Severity:** P1
- **Confidence:** Confirmed
- **Category:** Production security / availability
- **Location:** `config/settings/base.py`, `.env.example`, `apps/accounts/sms.py`, `config/settings/production.py`
- **Description:** The default SMS provider is console-oriented; unknown provider configuration can also fall back to console behavior. Production settings do not fail closed when a real provider is missing.
- **Trigger:** Deployment omits/mistypes `SMS_PROVIDER`.
- **Expected:** Production startup fails with a clear configuration error unless a supported production SMS provider is configured.
- **Actual:** OTPs can be written to logs while real users receive nothing; operational logs now contain authentication secrets during their validity window.
- **Root cause:** Development fallback is allowed in production configuration.
- **Recommended fix:** Validate provider at startup in production, reject console/null/unknown provider unless an explicit emergency flag is intentionally enabled, redact OTPs from production logs, and add deployment checks.

---

## 5. Medium Priority Issues — P2

### AUD-016 — Stock confirmation form ignores the submitted validity period

- **Severity:** P2
- **Confidence:** Confirmed
- **Category:** Backend/UI state
- **Location:** `apps/inventory/forms.py::ItemStockForm`, `apps/inventory/views.py::lot_confirm_stock`
- **Description:** Form accepts `stock_valid_for_days`, but the view calls `confirm_item_stock()` without passing the value.
- **Trigger:** Seller changes validity on the stock-confirmation screen.
- **Expected:** New validity is persisted and controls expiry.
- **Actual:** The old value remains while UI reports successful confirmation.
- **Root cause:** Missing parameter in view→service call.
- **Recommended fix:** Pass/persist validated validity in the same transaction; regression-test through the HTTP view.

### AUD-017 — OTP verification/request rate limiting is race-prone and mostly phone-scoped

- **Severity:** P2
- **Confidence:** Highly likely
- **Category:** Authentication security / concurrency
- **Location:** `apps/accounts/services.py`
- **Description:** Challenge attempt/use updates and request-count checks are not consistently serialized with `select_for_update`/conditional atomic updates. Parallel requests can race cooldown/attempt counters. Captured IP is not a strong global/per-IP abuse control.
- **Trigger:** Concurrent OTP requests/verifications or distributed attempts over many phone numbers.
- **Expected:** Max attempts, one-time use, cooldown, and abuse limits hold under concurrency.
- **Actual:** Lost updates/double-success/provider-cost abuse are possible under load; SQLite tests cannot prove PostgreSQL behavior.
- **Root cause:** Read-check-write OTP state without a concurrency-specific design.
- **Recommended fix:** Use transactional row locking or conditional updates for challenge use/attempts; add per-phone + per-IP + global/provider throttles; test on PostgreSQL.

### AUD-018 — Media type validation trusts filename-derived information and does not validate file contents

- **Severity:** P2
- **Confidence:** Confirmed
- **Category:** File upload security
- **Location:** `apps/inventory/services.py::_classify_upload`
- **Description:** Extension, browser content type, and `mimetypes.guess_type(filename)` are used; guessed MIME is itself derived from the filename. Arbitrary bytes renamed to an allowed extension can pass classification.
- **Trigger:** Upload non-image/non-video content with `.jpg`/`.mp4` and compatible/fake MIME.
- **Expected:** File is proven to be a supported image/video container before storage/use.
- **Actual:** Classification can accept invalid content.
- **Root cause:** Metadata validation without content verification.
- **Recommended fix:** Decode/verify images with Pillow and re-open safely; validate video container/signature with an appropriate safe library/tool; retain size limits and restrict supported formats.

### AUD-019 — Deleting media/product database rows can leave storage objects orphaned

- **Severity:** P2
- **Confidence:** Confirmed
- **Category:** Storage / data lifecycle
- **Location:** `apps/inventory/services.py::delete_lot_media`, `delete_item`
- **Description:** Code deletes `LotMedia` database rows but does not explicitly delete `FileField`/thumbnail objects from storage. Django does not automatically guarantee file removal on model deletion.
- **Trigger:** Seller removes media or hard-deletes a product without history.
- **Expected:** Unreferenced large media objects are deleted after the DB transaction safely commits.
- **Actual:** Local/S3 blobs can accumulate and old URLs may remain accessible depending on storage setup.
- **Root cause:** DB lifecycle and object-storage lifecycle are not coordinated.
- **Recommended fix:** Delete storage objects with `transaction.on_commit` (or a reliable cleanup service), including thumbnails; add mocked-storage tests and periodic orphan audit tooling.

### AUD-020 — Multiple primary product images are possible under concurrency

- **Severity:** P2
- **Confidence:** Confirmed
- **Category:** Concurrency / media integrity
- **Location:** `apps/inventory/models.py::LotMedia`, `apps/inventory/services.py::add_lot_media`, `set_primary_media`
- **Description:** No conditional unique constraint enforces one `is_primary=True` media per item. Check/update/create operations are not serialized on the item.
- **Trigger:** Two users/uploads set primary concurrently.
- **Expected:** At most one primary image.
- **Actual:** More than one primary row can be committed.
- **Root cause:** Application-only invariant.
- **Recommended fix:** Add `UniqueConstraint(fields=["lot"], condition=Q(is_primary=True))` and lock the item when switching primary; handle conflict deterministically.

### AUD-021 — A buyer Business can read seller draft invoices

- **Severity:** P2
- **Confidence:** Confirmed
- **Category:** Authorization / document lifecycle
- **Location:** `apps/invoicing/selectors.py::invoices_for`, `get_invoice`
- **Description:** Invoice visibility is `seller OR buyer` without status filtering.
- **Trigger:** Seller creates a draft invoice with a buyer Business.
- **Expected:** Draft document is seller-internal until issued, unless collaboration on drafts is explicitly designed.
- **Actual:** Buyer can list/open the draft.
- **Root cause:** Counterparty authorization ignores document status.
- **Recommended fix:** Seller sees all own invoices; buyer sees issued documents (and any intentionally retained cancelled documents according to policy). Add negative tests for draft visibility.

### AUD-022 — Invoice number allocation scans historical invoices while holding a seller lock

- **Severity:** P2
- **Confidence:** Confirmed
- **Category:** Performance / concurrency
- **Location:** `apps/invoicing/services.py::allocate_number`
- **Description:** Allocation iterates historical invoice numbers in Python to find the maximum while the seller Business row is locked.
- **Trigger:** Business accumulates many invoices and multiple salespeople issue concurrently.
- **Expected:** O(1) or indexed O(log n) sequence allocation with uniqueness.
- **Actual:** Transaction time and lock contention grow with invoice history.
- **Root cause:** String invoice number is used as the sequence source.
- **Recommended fix:** Store a numeric sequence/counter, use an indexed numeric max or per-Business counter row, retain a DB unique constraint on `(seller, number)`.

### AUD-023 — Django Admin can mutate/delete historical Trade/Invoice data outside domain workflows

- **Severity:** P2
- **Confidence:** Confirmed
- **Category:** Operational data integrity
- **Location:** `apps/invoicing/admin.py`, `apps/trading/admin.py`
- **Description:** Ledger admin is intentionally read-only, but invoice/trade admins do not fully prohibit deletion or mutation of finalized historical identity/state.
- **Trigger:** Platform superuser edits/deletes a Trade or issued invoice in Django Admin.
- **Expected:** Historical commercial records follow explicit cancel/reversal/correction workflows.
- **Actual:** Admin can bypass normal service invariants where FK protection does not happen to block the action.
- **Root cause:** Domain immutability is enforced in services/UI but not administration.
- **Recommended fix:** Make finalized Trade and issued/cancelled Invoice admin views read-only for commercial fields; disable hard delete; provide explicit controlled corrective operations.

### AUD-024 — Hard-deleting a Business can cascade through tenant-owned data

- **Severity:** P2
- **Confidence:** Potential risk
- **Category:** Destructive action / data loss
- **Location:** `apps/businesses/admin.py`, multiple `ForeignKey(... on_delete=CASCADE)` tenant ownership relationships
- **Description:** Business admin does not disable delete. Many tenant-owned records cascade from Business; some counterparty PROTECT links may prevent deletion in certain datasets, but safety depends on incidental relationships.
- **Trigger:** Platform Admin deletes a Business in Django Admin.
- **Expected:** Real tenants are suspended/archived; irreversible tenant purge requires an explicit audited procedure.
- **Actual:** A Business with a deletable graph may cascade-delete products/inquiries and other operational data.
- **Root cause:** Technical admin hard-delete remains a normal action.
- **Recommended fix:** Disable normal Business hard-delete, implement suspend/archive, and create a separate deliberate purge command only if legally/operationally required.

### AUD-025 — Browse-only/expired Business UI exposes seller-only actions

- **Severity:** P2
- **Confidence:** Confirmed
- **Category:** UX / authorization consistency
- **Location:** `templates/layouts/app_shell.html`, `apps/businesses/context_processors.py`
- **Description:** Navigation uses member capability strings for many actions but does not consistently combine them with Business entitlements. Owner capability bypass means a browse-only owner still sees actions such as the central “add product” control and can enter pages that later fail in services.
- **Trigger:** Owner Business is browse-only or expired.
- **Expected:** UI reflects plan entitlement and clearly explains restricted functionality.
- **Actual:** Seller actions remain visible and end in error/denied flows.
- **Root cause:** Capabilities and entitlements are correctly separated in backend design but not consistently composed in presentation.
- **Recommended fix:** Gate navigation/CTAs using both capability and entitlement, while retaining server enforcement. Add browse/expired navigation tests.

### AUD-026 — Dynamic catalog resolution materializes all matched IDs in Python for some hybrid cases

- **Severity:** P2
- **Confidence:** Confirmed
- **Category:** Performance
- **Location:** `apps/catalog/selectors.py` hybrid catalog resolution
- **Description:** Rule matches can be converted to a Python `set` of all primary keys before manual includes/excludes are applied.
- **Trigger:** Large rule-based catalog with manual include/exclude overrides.
- **Expected:** Filtering stays in SQL and remains pageable.
- **Actual:** Memory and query cost grow with the entire matching set.
- **Root cause:** Python set algebra is used where SQL OR/NOT or queryset union can express the rule.
- **Recommended fix:** Compose Q objects/querysets in SQL; apply manual include/exclude predicates without materializing all IDs.

### AUD-027 — No CI release gate or protected main branch

- **Severity:** P2
- **Confidence:** Confirmed
- **Category:** DevOps / quality assurance
- **Location:** Repository configuration / current branch status
- **Description:** The audited branch head has no CI/status checks, and `master` is not protected with required checks.
- **Trigger:** Code is merged/pushed despite failing tests, migrations, lint, or checks.
- **Expected:** Required automated checks block regressions before merge.
- **Actual:** Repository process provides no enforced gate.
- **Root cause:** CI/branch protection has not been configured.
- **Recommended fix:** Add GitHub Actions for pytest, Django check, `makemigrations --check`, fresh migration, Ruff, and a PostgreSQL integration job; protect master and require passing checks/review.

### AUD-028 — Default test suite forces SQLite for logic that relies on PostgreSQL locking/constraints

- **Severity:** P2
- **Confidence:** Confirmed
- **Category:** Testing gap
- **Location:** `conftest.py`
- **Description:** Pytest defaults to SQLite. Critical financial and OTP behavior relies on `select_for_update`, concurrency, and conditional unique constraints whose runtime behavior differs from PostgreSQL.
- **Trigger:** Concurrency bug exists but SQLite tests pass.
- **Expected:** Critical persistence workflows are tested against the production database engine.
- **Actual:** Default tests cannot validate production locking semantics.
- **Root cause:** Fast local suite is the only documented/default gate.
- **Recommended fix:** Keep fast SQLite unit tests if useful, but add mandatory PostgreSQL integration/concurrency CI tests for trading, invoice numbering/idempotency, ledger, OTP, media primary uniqueness, and migrations.

### AUD-029 — Provided container/deployment path is development-oriented, not production-ready

- **Severity:** P2
- **Confidence:** Confirmed
- **Category:** Deployment
- **Location:** `Dockerfile`, `docker-compose.yml`, `docs/deployment.md`
- **Description:** Docker image installs `requirements/development.txt`, contains build tooling, runs as root, and defaults to Django `runserver`. Compose mounts source and publishes database/Redis ports. Production guide is a checklist rather than a deployable/run-tested setup.
- **Trigger:** Operator treats repository Docker setup as production deployment.
- **Expected:** Gunicorn (or equivalent), non-root image, production dependencies, static collection, health/restart strategy, migration strategy, private DB/Redis networking, and tested backup/restore runbook.
- **Actual:** Development server/tooling are the executable defaults.
- **Root cause:** Production hardening was deferred.
- **Recommended fix:** Create a production image/entrypoint and documented deployment topology; use Gunicorn; drop root; separate build/runtime deps; collect static; define migrations/restarts/health/backups.

### AUD-030 — Third-party frontend scripts are loaded without SRI/CSP hardening

- **Severity:** P2
- **Confidence:** Potential risk
- **Category:** Frontend security / supply chain
- **Location:** `templates/base.html`, production security settings
- **Description:** Alpine and HTMX are loaded from `unpkg.com` without Subresource Integrity, while no explicit Content-Security-Policy is visible. Google Fonts are also remote.
- **Trigger:** CDN compromise, unexpected content change, or permissive script execution policy.
- **Expected:** Critical JS is self-hosted or cryptographically pinned and constrained by CSP.
- **Actual:** Browser trusts third-party script responses directly.
- **Root cause:** Convenience CDN setup carried into production template.
- **Recommended fix:** Prefer self-hosted pinned JS assets, or add verified SRI/crossorigin; introduce a practical CSP and related headers without breaking HTMX/Alpine.

### AUD-031 — Public inquiry UX is fragmented and direct forms omit the required per-product quantity flow

- **Severity:** P2
- **Confidence:** Confirmed
- **Category:** Product/UX consistency
- **Location:** `templates/catalog/lot_detail.html`, `apps/catalog/views_public.py`, multi-product cart/OTP flow
- **Description:** Product detail offers a direct “استعلام / مشاوره” name/phone/message form, while the V2 designed flow is select product(s) → quantity per item → identity → OTP. Shared-catalog general inquiry similarly can be submitted without selected item quantities.
- **Trigger:** Customer uses the most obvious product-detail inquiry button rather than the selection cart.
- **Expected:** Core inquiry data contains selected products and requested sqm consistently.
- **Actual:** Seller may receive an inquiry with no requested quantity/items and an unverified phone (also AUD-009).
- **Root cause:** Legacy low-friction forms remain alongside the new workflow.
- **Recommended fix:** Consolidate CTA behavior into the multi-product selection/quantity/OTP flow. Keep a separate “general question” only if intentionally modeled and labeled differently.

### AUD-032 — Some financial report/statement totals have ambiguous lifecycle/filter semantics

- **Severity:** P2
- **Confidence:** Confirmed
- **Category:** Reporting correctness / UX
- **Location:** `apps/reporting/reports.py::invoice_summary`, `apps/accounting/selectors.py::statement_totals`
- **Description:** Invoice summary total excludes cancelled invoices but includes drafts, even though issued/draft lifecycle differs. Statement `closing` uses the last matching row's stored `balance_after`; when filtering by entry type, that running balance includes effects of entries hidden by the filter.
- **Trigger:** Draft invoices exist or statement is filtered to one entry type/date subset.
- **Expected:** Totals clearly correspond to the rows/statuses the label claims, with opening/closing balance semantics explicit.
- **Actual:** Users can read a total/closing figure that is not the sum/effect of visible rows.
- **Root cause:** Stored global running balance and filtered reporting are mixed without explicit opening/closing policy.
- **Recommended fix:** Define report semantics: issued invoice totals should normally sum issued only; statements should show opening balance + period movements + closing balance, or clearly label current/global balance separately from filtered row totals.

---

## 6. Low Priority Issues — P3

### AUD-033 — Logout accepts GET

- **Severity:** P3
- **Confidence:** Confirmed
- **Category:** Security hygiene / UX
- **Location:** `apps/accounts/views.py::logout_view`
- **Description:** Logout is allowed with GET or POST.
- **Trigger:** External page/image/link causes navigation to logout URL.
- **Expected:** State-changing logout should normally be POST+CSRF.
- **Actual:** Cross-site navigation can force a logout.
- **Root cause:** Convenience GET support.
- **Recommended fix:** Restrict to POST and use a CSRF-protected form/button.

### AUD-034 — Dependency builds are not fully reproducible

- **Severity:** P3
- **Confidence:** Confirmed
- **Category:** Dependency management
- **Location:** `requirements/base.txt`, `requirements/development.txt`
- **Description:** Dependencies use broad compatible ranges rather than an audited lock/constraints file.
- **Trigger:** Two builds occur at different times after upstream releases.
- **Expected:** Production rebuild uses the same reviewed dependency versions unless intentionally updated.
- **Actual:** Patch/minor versions can drift inside ranges.
- **Root cause:** Range-based requirements are used as both policy and deploy resolution.
- **Recommended fix:** Keep human-readable ranges if desired but generate/review a pinned constraints/lock file for production; add dependency vulnerability scanning.

### AUD-035 — Django REST Framework is installed/configured without active API routes

- **Severity:** P3
- **Confidence:** Confirmed
- **Category:** Maintainability / dependency cleanup
- **Location:** `requirements/base.txt`, settings, `config/urls.py`
- **Description:** DRF is present but no application API routes were found in the active URL configuration.
- **Trigger:** Ongoing maintenance/security updates for an unused dependency.
- **Expected:** Dependencies represent active needs.
- **Actual:** Extra framework surface remains without clear current use.
- **Root cause:** Earlier architecture/plans left a dependency behind.
- **Recommended fix:** Remove DRF until an API is actually needed, unless a hidden/admin integration depends on it; verify imports first.

### AUD-036 — Duplicating a product silently drops special-sale price details

- **Severity:** P3
- **Confidence:** Highly likely
- **Category:** UX / business logic
- **Location:** `apps/inventory/services.py::duplicate_item`
- **Description:** Normal price fields are copied, but `special_amount` / `special_until` are not passed when cloning prices.
- **Trigger:** Seller duplicates an item currently configured for special sale.
- **Expected:** Either promotional settings are copied or the UI clearly says promotions are intentionally reset.
- **Actual:** Promotion settings silently disappear.
- **Root cause:** Clone field list is incomplete or policy is undocumented.
- **Recommended fix:** Decide intended behavior; copy safely if expected, otherwise explicitly reset with a visible notice/test.

### AUD-037 — Legacy operational concepts remain exposed in technical admin/code

- **Severity:** P3
- **Confidence:** Confirmed
- **Category:** Cleanup / maintainability
- **Location:** `apps/businesses/admin.py` (Warehouse inline/admin), notification legacy enum and migration-only apps
- **Description:** User-facing Warehouse workflow is removed, but Warehouse remains editable in Django Admin; some legacy matching/saved-search naming remains in technical code for migration/history.
- **Trigger:** Platform operator assumes legacy admin objects are still supported product concepts.
- **Expected:** Migration-only structures are clearly marked/read-only or removed when migration history permits.
- **Actual:** Technical admin can still create/edit obsolete Warehouse records.
- **Root cause:** Runtime cleanup is incomplete after data migration.
- **Recommended fix:** Remove/hide legacy admin surfaces first; retain migration models/packages only as long as migration graph requires; document them as historical.

---

## 7. Security Findings

The strongest security improvement in V2 is the platform-provisioning boundary: login OTP no longer creates Users, unknown phone numbers do not receive real SMS, customer OTP uses a separate purpose, and public pricing queries avoid loading B2B tiers. Public specification templates also explicitly allowlist safe fields rather than iterating model attributes.

Release-blocking security/account-lifecycle issues remain: AUD-004 (suspended tenant access), AUD-006 (verification not enforced), AUD-008 (permission re-grant), AUD-009 (customer OTP bypass), and AUD-015 (production console OTP fallback). Additional hardening is required for OTP concurrency/abuse (AUD-017), uploaded media content verification (AUD-018), admin/destructive actions (AUD-023/024), and third-party frontend script integrity/CSP (AUD-030).

No direct SQL-injection or command-injection pattern was found in the inspected Django ORM/form code. CSRF protection is generally present through Django forms and POST endpoints; logout GET is a low-severity exception. Object-level tenant scoping is generally deliberate and stronger than the previous version, but business lifecycle status must become a centralized authorization input.

---

## 8. Database/Data Integrity Findings

Positive controls include: immutable ledger rows, reversal-based correction, positive-amount checks, unique live trade-ledger constraints, Trade→PurchaseRequest OneToOne finalization, invoice line snapshots, product soft-delete when history exists, and centralized buyer eligibility.

The main integrity gaps are AUD-001 (invoice-per-Trade uniqueness), AUD-002/003 (incomplete financial event model), AUD-010 (partial public submission), AUD-014 (privacy-widening migration), AUD-019/020 (media storage/primary invariants), and AUD-024 (tenant hard delete). Important business invariants such as stock-validity ranges, Product/InventoryLot tenant consistency, and stock-mode/quantity consistency are still enforced primarily in forms/services rather than comprehensive database constraints; those should be hardened after the P1 workflow fixes.

The accounting V2 backfill is intentionally conservative: it maps legacy ledger counterparties only where `Contact.linked_business` is reliable and preserves unmapped legacy history rather than inventing ownership. That is a good migration decision.

---

## 9. Backend Findings

Service/selector separation is generally good and the V2 code is substantially easier to reason about than the previous demand-board architecture. `inventory.policy` is an especially useful boundary: public/B2B/catalog paths now share one visibility/availability policy and audience-specific price prefetching.

The most important backend weaknesses are policy composition and workflow atomicity. Business lifecycle, verification, entitlement, membership capability, and historical-account access are each valid concepts but are not consistently combined. Several workflows also stop one layer too early: the manual invoice does not own a Trade; the buyer side of finalization is not posted; multi-seller inquiries do not own one submission transaction; the product wizard is split across atomic service calls rather than one atomic use case.

---

## 10. Frontend Findings

The frontend is conventional Django templates + HTMX/Alpine with no Node build step. The audited templates are generally readable and RTL-oriented. Product sharing, media gallery inclusion, B2C-safe specs, print invoices, and mobile bottom navigation are present.

Key frontend problems are not JavaScript runtime complexity but **workflow mismatch**: browse-only owners see unavailable seller actions (AUD-025), product detail bypasses the designed quantity/OTP flow (AUD-031), and hard result caps make later products unreachable (AUD-013). The product wizard exposes an action to staff that their role cannot complete (AUD-011).

A live browser/device audit was not possible here, so CSS breakpoint behavior, keyboard focus order, color contrast, screen-reader announcements, browser-back state, and slow-network HTMX behavior remain runtime validation items rather than confirmed defects.

---

## 11. UI/UX Findings

V2 navigation is much clearer than the previous version: Demand Board and manual Contacts are gone from primary navigation, “موجودی من / بازار / خرید و فروش / کاتالوگ‌ها / بیشتر” matches the real product more closely, and availability/freshness terminology is clearer.

The main UX principle still needing enforcement is **one business event = one obvious workflow**. Currently a colleague sale can be represented by a manual invoice without financial posting, while a direct-sale service exists elsewhere; public inquiry can happen through multiple inconsistent paths; and plan/permission restrictions are discovered late. These are usability problems because they create contradictory outcomes, not merely cosmetic inconsistencies.

---

## 12. Performance Findings

The most material scaling issues are missing pagination (AUD-013), Python materialization of dynamic catalog IDs (AUD-026), and O(n) invoice-number allocation under a lock (AUD-022). Query architecture otherwise shows good awareness of `select_related`/`prefetch_related` and database aggregation.

Media optimization/transcoding is intentionally limited for MVP, which is reasonable, but production should enforce file-content validity and storage lifecycle before large video usage. Public images/video delivery should rely on suitable object storage/CDN behavior rather than Django workers.

---

## 13. Testing Gaps

The repository contains meaningful unit/integration tests, including strong tests for unknown-phone provisioning boundaries, invoice snapshots, tenant isolation, plan entitlements, sale idempotency, dynamic catalogs, and ledger reversal. These are valuable assets.

High-risk missing regression categories identified by this audit:

- PostgreSQL concurrent invoice creation for one Trade.
- Concurrent OTP request/verification/attempt limits.
- Concurrent primary-media changes.
- Suspended/expired/unverified Business access across all app/public surfaces.
- Already-published seller items after downgrade/expiry.
- Empty explicit membership permissions.
- Manual colleague invoice vs Trade/ledger invariant.
- Buyer-side purchase accounting/report totals.
- Multi-seller inquiry failure in the middle + retry idempotency.
- Default staff completing product-create and sale→invoice workflows.
- Stock-validity HTTP form persistence.
- Freshness-aware price/stock search/filter/sort.
- Pagination preserving combined filters.
- V1 colleague-only visibility migration privacy.
- Production settings failing when SMS provider is unsafe/missing.
- File-content validation and object-storage cleanup.
- Fresh database migration and realistic V1→V2 upgrade on PostgreSQL.

The default `conftest.py` forces SQLite, so a second mandatory PostgreSQL CI lane is required even if the fast SQLite suite remains.

---

## 14. Production Readiness Assessment

| Category | Score | Assessment |
|---|---:|---|
| Functionality | **6/10** | Core V2 features are present, but several important workflows have contradictory or incomplete paths (invoice/ledger, inquiry, staff workflow, pagination). |
| Reliability | **5/10** | Good transaction usage in core sale/ledger code, but invoice race, partial inquiry submission, workflow atomicity gaps, and no production CI reduce confidence. |
| Security | **6/10** | Stronger tenant/public-price/OTP provisioning design, but suspended-account gating, permission re-grant, OTP bypass/fallback, upload validation, and CDN hardening remain. |
| Data integrity | **5/10** | Ledger/history design is strong; financial-event consistency, invoice uniqueness, migration privacy, media lifecycle, and destructive admin actions are not yet safe enough. |
| Performance | **6/10** | ORM use is generally sensible, but hard caps/no pagination, catalog ID materialization, and invoice sequence scanning will become visible with growth. |
| UX/UI | **6/10** | V2 navigation/terminology is improved; inconsistent public inquiry, plan/capability mismatch, and unreachable results still hurt first-time use. Live device/accessibility testing remains. |
| Maintainability | **7/10** | Modular monolith + service/selectors + shared eligibility/filter ideas are good. Legacy admin/dependency remnants and duplicated workflow paths should be removed. |
| Test coverage | **6/10** | Many valuable tests exist, but the highest-risk concurrency and PostgreSQL-specific behaviors are not part of a verified gate. |
| Deployment readiness | **3/10** | No CI/protected branch, no verified branch checks, development-oriented Docker default, unsafe SMS fallback, and incomplete production runbook. |

### Overall verdict

**Not ready for real-user production deployment.** The project is in a promising late-MVP engineering state, but the P1 financial/authorization/inquiry issues and production gate must be fixed before real businesses rely on the system.
