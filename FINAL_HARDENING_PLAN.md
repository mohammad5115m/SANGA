# SANGA — Final Hardening Plan

**Repository:** `mohammad5115m/SANGA`
**Base branch:** `cursor/sanga-v2-remediation-b749` (at `7f34d93`)
**Working branch:** `cursor/sanga-v2-final-hardening`
**Baseline:** 570 passed, 10 skipped (SQLite lane; the 10 skips are the PostgreSQL-only concurrency tests)

This is the second remediation pass. The V2 architecture is sound and is not being rewritten: the modular monolith, `Business`/`BusinessMembership` tenancy, capability authorization, `Product`/`InventoryLot` separation, immutable `LedgerEntry` with reversal, live catalogs against snapshot invoices, and the deliberate `PurchaseRequest → finalize → Trade` split all stay. What follows is scoped to defects that would cost a real business money, data or trust.

Every issue below was verified against the code on this branch before being written down. Two entries in the brief needed correcting, and they are marked as such.

---

# Phase 0 — Verification summary

| ID | Issue | Confirmed | Severity |
|----|-------|-----------|----------|
| P1-01 | Direct sale is not idempotent | Yes | Critical — financial duplication |
| P1-02 | Purchase-request transitions are not serialized | Yes | Critical — contradictory commercial state |
| P1-03 | Vulnerable and end-of-life dependencies | Yes, and worse than reported | High |
| P1-04 | Production media storage can silently lose files | Yes | High |
| P1-05 | No multi-line commercial sale | Yes | High — product architecture |
| P1-06 | Network eligibility treats verification as a denylist | Yes | High |
| P2-01 | OTP request throttling races; `X-Forwarded-For` is trusted | Yes | Medium-high |
| P2-02 | Public surfaces disagree about seller eligibility | Yes | Medium |
| P2-03 | Notifications are role-based, not capability-based | Yes | Medium |
| P2-04 | Media processing hardening | Partially already done | Medium |
| P2-05 | Product taxonomy is uncontrolled free text | Yes | Medium |
| P2-06 | Branch protection undocumented | Yes | Low |
| SMS | No real SMS gateway exists | Yes | Blocks production |

---

# P1-01 — Make direct sale idempotent

**Confirmed.** `record_direct_sale()` in `apps/trading/services.py` (lines 326-401) calls `Trade.objects.create()` unconditionally. There is no submission token, no unique constraint and no row lock anywhere in the function.

**Root cause.** Idempotency for the request-driven path is a side effect of `Trade.purchase_request` being a `OneToOneField` plus the `select_for_update()` in `finalize_sale()`. A direct sale has no `PurchaseRequest`, so it inherits neither protection. `uniq_trade_entry_per_trade` cannot help: two submissions produce two distinct `Trade` rows, so both ledger posts are legitimately unique and the colleague's balance moves twice.

**Affected files.** `apps/trading/models.py`, `apps/trading/services.py`, `apps/trading/forms.py`, `apps/trading/views.py`, `templates/trading/direct_sale.html`.

**Implementation.**

1. `Trade.submission_id = UUIDField(null=True, blank=True, editable=False)`.
2. `UniqueConstraint(fields=["seller_business", "submission_id"], condition=Q(submission_id__isnull=False), name="uniq_trade_per_submission")`. Scoped by seller, partial so historical rows and non-form callers stay legal. This mirrors `uniq_inquiry_per_submission_and_seller`, which already solved exactly this problem for public inquiries.
3. `DirectSaleForm` mints the token on GET and carries it in a hidden field, so a refresh, a double-click and a proxy retry all present the same value.
4. `record_direct_sale(..., submission_id=None)` locks the seller `Business` row, re-checks for an existing trade under that lock, creates inside a savepoint, and on `IntegrityError` re-fetches and returns the winner.

**Migration.** Additive: one `AddField` plus one `AddConstraint`. No backfill — `NULL` means "not submitted through the form", which is what pre-existing rows are.

**Backward compatibility.** `submission_id` is optional, so every existing caller and test keeps working unchanged.

**Concurrency strategy.** Database constraint first, lock second, `IntegrityError` recovery third. Never `if not exists: create()` alone.

**Tests.** PostgreSQL concurrency test racing two `record_direct_sale()` calls with one token, asserting exactly one `Trade`, one seller `SALE`, one buyer `PURCHASE`, one balance movement, one invoice, no leaked `IntegrityError`. Plus a sequential-retry test on the SQLite lane.

**Product impact.** None visible. The user sees one sale where they performed one sale.

---

# P1-02 — Lock all purchase-request state transitions

**Confirmed.** `finalize_sale()` (line 267) re-fetches under `select_for_update()`. `cancel_purchase_request()` (line 158) and `respond_to_purchase_request()` (line 174) both validate and write against the caller-supplied in-memory instance.

**Root cause.** Status validation reads a stale object. `transaction.atomic` alone provides no protection here, because under PostgreSQL's default READ COMMITTED two transactions can each read `ACCEPTED` and each write a different terminal status. The dangerous outcome is a `CANCELLED` request that owns a `Trade`, a ledger pair and an invoice.

**Affected files.** `apps/trading/services.py`, `apps/trading/views.py`.

**Implementation.**

1. An explicit `ALLOWED_TRANSITIONS` map: `SENT → {ACCEPTED, REJECTED, CANCELLED}`, `ACCEPTED → {COMPLETED, CANCELLED}`, and nothing out of `COMPLETED`, `REJECTED` or `CANCELLED`.
2. One private helper that re-fetches the row with `select_for_update()` inside `transaction.atomic`, validates the transition *after* acquiring the lock, and refuses anything not in the map.
3. All three services route through it.
4. A cross-table guard: refuse `CANCELLED`/`REJECTED` when a `Trade` already references the request.

**Why no database constraint.** "A request with a trade is not cancelled" spans two tables. PostgreSQL cannot express that as a `CheckConstraint`, and a trigger would put commercial logic somewhere no reader of the service layer will look. The row lock plus the existing `OneToOneField` is the enforcement; the concurrency tests are the proof. This is a deliberate decision, recorded here rather than silently taken.

**Migration.** None. Behaviour only.

**Tests.** Five PostgreSQL races — accept/reject, cancel/accept, cancel/finalize, finalize/finalize, and a stale browser POST after `COMPLETED` — each asserting both the final status and the commercial side effects.

---

# P1-03 — Resolve dependency advisories

**Confirmed, and the brief understates it.** `requirements/constraints.txt` pins `Django==5.1.15` under a `Django>=5.1,<5.2` range. Django 5.1 reached end of life on 2025-12-31, so it receives no security patches at all — the problem is not one advisory but an unsupported branch. `pillow==11.3.0` carries CVE-2026-25990, CVE-2026-40192, CVE-2026-42308, CVE-2026-42309, CVE-2026-42310 and CVE-2026-42311, all fixed in 12.2.0. Pillow matters more than usual here because it is the code path that touches untrusted uploads.

**Implementation.** Move to Django 5.2 LTS (supported to April 2028) and Pillow ≥ 12.2, widening the ranges in `requirements/base.txt` and re-pinning `requirements/constraints.txt`. Remove the now-deprecated `FORMS_URLFIELD_ASSUME_HTTPS` setting, which already emits `RemovedInDjango60Warning` on the current baseline.

**Audit policy.** `pip-audit` currently runs with `continue-on-error: true`. If the upgrade produces a clean audit the step becomes blocking. If any advisory genuinely has no upstream fix, it is documented individually with a rationale rather than the whole audit being hidden.

**Tests.** The full suite, plus `check`, `check --deploy` and `makemigrations --check`, re-run after the upgrade.

---

# P1-04 — Make production media storage safe

**Confirmed.** `config/settings/production.py` validates `SECRET_KEY`, `ALLOWED_HOSTS` and the SMS provider, but says nothing about storage. With `USE_S3=false` the default storage stays `FileSystemStorage` writing to `/app/media`; `config/urls.py` line 25 only routes `MEDIA_URL` when `DEBUG`; and `docker-compose.prod.yml` line 84 declares only `postgres_data`. Uploads therefore land in the container's writable layer, are unreachable over HTTP, and vanish on the next deploy.

**Implementation.** SANGA production requires object storage.

- `USE_S3=false` raises `ImproperlyConfigured` unless `SANGA_ALLOW_LOCAL_MEDIA=true` is set as a deliberate, named override.
- `USE_S3=true` validates bucket name, credentials and region-or-endpoint, and that the media origin appears in `CSP_IMG_SRC`/`CSP_MEDIA_SRC` — otherwise the browser blocks every product image and the failure looks like a data problem.
- The local-media override requires a persistent `MEDIA_ROOT` and is documented as needing both a mounted volume and a reverse proxy serving `/media/`. `docker-compose.prod.yml` gains the volume so the supported path exists.

No secrets are hardcoded; everything is read from the environment.

**Tests.** Configuration tests proving each unsafe combination fails closed and each safe one boots.

**Docs.** `docs/deployment.md` and `.env.example`.

---

# P1-05 — Support realistic multi-line B2B sales

**Confirmed.** `Trade` carries one product snapshot (`product_name`, `stone_type`, `grade`, `quantity_sqm`, `unit_price`), `DirectSaleForm` exposes one product row, and `create_invoice_for_trade()` writes exactly one `SalesInvoiceItem`.

**Root cause.** A real stone seller sells three stones to one colleague in one conversation. The current model forces that into three trades, three invoices and three ledger entries — so the workaround for a modelling gap is worse bookkeeping than the thing it works around.

**Target shape.** `Trade` becomes a commercial header; `TradeItem` carries the lines. One `Trade` → many `TradeItem` → one total → one seller ledger entry → one buyer ledger entry → one `SalesInvoice` → many `SalesInvoiceItem`.

**Migration strategy — staged and non-destructive.**

1. Create `TradeItem`.
2. Backfill exactly one `TradeItem` per existing `Trade`, copying the snapshot columns verbatim, and assert the sum of line totals equals the trade total before and after.
3. Move reads and writes onto `TradeItem`.
4. **Do not drop the legacy `Trade` snapshot columns in this pass.**

The legacy columns stay populated for single-line trades — which is every historical row and every purchase-request sale — so history and any overlooked reader keep working. They are left blank for multi-line trades, where `TradeItem` is the only truth. Removing them is a separate, later change made once nothing reads them.

**Reporting fan-out.** Summing `Trade.total_amount` across a join to `TradeItem` multiplies the money by the line count. So `sales_by_stone_type` and `sales_by_product` re-base onto `TradeItem.line_total`, while `sales_by_colleague` and `sales_summary` keep money on `Trade` and take quantity from a separate `TradeItem` aggregate.

**Invariants preserved.** Invoices stay historical snapshots. `TradeItem` is immutable history too. Renaming or deleting a product never rewrites a past trade. Stock is still never decremented automatically. One sale is still one financial total, not one ledger entry per line.

**Tests.** Multi-line direct sale; one invoice with several rows; ledger amount equal to the sum of lines; rounding; snapshot immutability after rename and delete; idempotent multi-line submission; query-count guards on the detail and report pages.

---

# P1-06 — Enforce network verification policy

**Confirmed.** `business_is_network_eligible()` (`apps/businesses/eligibility.py` lines 93-106) rejects only `REJECTED` and `SUSPENDED`, so `UNVERIFIED` and `PENDING` participate. `SANGA_REQUIRE_VERIFIED_FOR_NETWORK` is read through `getattr(settings, ..., False)` and is defined in no settings module, so the strict path is dead code.

**Root cause.** The denylist was chosen because `verification_status` defaults to `unverified` and nothing in provisioning sets it, so an allowlist would have emptied every directory on the day it shipped. That reasoning was correct; the fix is to make provisioning set the field and backfill the existing rows, not to keep the policy loose.

**Implementation.** Require `VERIFIED` in both the Python and SQL halves, default the policy on in production, expose verification in the platform admin flow, and add a data migration marking every currently `ACTIVE`, non-rejected, non-suspended `Business` as `VERIFIED`.

The backfill preserves today's visibility exactly and exposes nothing that was previously restricted — every business it touches is already visible. From then on the policy binds all new tenants.

**Historical access is explicitly out of scope.** `accounting_counterparty()` and `invoices_for()` deliberately do not consult network eligibility, so a suspended debtor keeps an openable statement and a settleable balance. That stays.

**Tests.** All five verification states across directory, marketplace, public product visibility, purchase-request creation, and historical invoice/ledger access.

---

# P2-01 — Make OTP request throttling concurrency-safe

**Confirmed.** `_enforce_request_limits()` (`apps/accounts/services.py` lines 85-102) runs `.first()` and two `.count()` queries outside any transaction; the challenge is inserted afterwards in a separate `transaction.atomic()`. Two simultaneous requests both pass. Separately, `_client_ip()` (lines 59-65) returns the leftmost `X-Forwarded-For` value unconditionally, so any client can defeat the per-IP cap by sending a header.

**Implementation.** A dedicated `OtpRequestThrottle` row keyed `UniqueConstraint(["scope", "key"])` with a rolling window, locked with `select_for_update()`. The check and the challenge insert become one serialized transaction; the phone row is locked before the IP row, in that fixed order, so two requests from one phone behind one address cannot deadlock. SMS delivery moves to `transaction.on_commit` so a rolled-back request never sends a code.

For proxies, a new `SANGA_TRUSTED_PROXY_COUNT` (default `0`) makes `_client_ip()` read the Nth-from-right hop. With the default it ignores the header entirely. The deployment docs state that the edge proxy must overwrite, not append, `X-Forwarded-For`.

**Tests.** Concurrent OTP requests against the cooldown and both hourly caps; spoofed-header tests proving the client cannot choose its own rate-limit key.

---

# P2-02 — Align public surfaces with eligibility

**Confirmed.** `_business_or_404()` (`apps/catalog/views_public.py` line 32) filters on `status=Business.Status.ACTIVE` alone, while the products on the same page go through `eligible_items()`, which applies the full `can_sell_q`. An expired, unverified or browse-only seller therefore gets a storefront shell with a name and header and zero products. `get_shareable_catalog()` has the same shape, gating only on `is_publicly_accessible`.

**Implementation.** One shared public-business resolver used by the storefront, product detail, share token, shared catalog and search, returning a generic unavailable response rather than an empty shell. The marketplace and directory already agree and are left alone. Accounting and invoice history stay outside the check.

**Tests.** Each surface, for an eligible and an ineligible seller, asserting no information about hidden or withdrawn products leaks.

---

# P2-03 — Make notifications capability-aware

**Confirmed.** `_notify_business()` (`apps/trading/services.py` line 76) and `_notify_seller()` (`apps/inquiries/services.py` line 279) both filter `role__in=[OWNER, MANAGER]`. The default `staff` role holds `SALE_FINALIZE`, `PURCHASE_REQUEST` and `LEADS_MANAGE` but no notification ever reaches it, so the person who does the work never hears about it.

**Implementation.** A reusable `members_with_capability(business, capability)` resolver, with each notification naming the capability needed to act on it: inquiries to `LEADS_MANAGE`, incoming purchase requests to `PURCHASE_REQUEST`, finalization to `SALE_FINALIZE`.

Filtering happens in Python over active memberships rather than in SQL, because `has_capability` combines an owner bypass with a JSON list and JSON containment lookups do not work on SQLite. Memberships are seat-limited, so this is a handful of rows and one query.

**Tests.** Default roles and custom permission sets, including a member who holds the capability without the role and an owner whose stored permission list is empty.

---

# P2-04 — Harden media processing further

**Partially already done — correcting the brief.** `apps/inventory/services.py` already enforces a 10 MB image and 60 MB video byte limit before decoding, and `media_validation.py` already opens the image twice so that `verify()` checks the container and `load()` forces pixel decode, which is what catches truncation. Extension and `Content-Type` are already treated as untrusted. Storage cleanup already runs on `transaction.on_commit`.

**What is actually missing.** No cap on dimensions or total pixel count, and no handling of Pillow's decompression-bomb error — a 200-megapixel PNG that compresses to a few kilobytes passes the byte limit and is then fully decoded into memory.

**Implementation.** Explicit maximum dimension and total-pixel limits, decompression-bomb errors converted into the same controlled validation error as any other bad upload, and `nosniff` plus a correct stored `Content-Type` on media objects.

**Video.** Full validation is deliberately deferred rather than pulling ffmpeg into the image for an MVP. The container signature check, the size cap and the restricted container list stay, and the limitation is documented instead of being implied to be more than it is.

**Tests.** Decompression bomb, oversized dimensions, oversized video, and the existing truncated and disguised-file cases.

---

# P2-05 — Normalize and control product taxonomy

**Confirmed.** `stone_type`, `primary_color`, `quarry_region`, `grade` and `processing_type` are free-text `CharField`s. `normalize_persian_text()` exists in `apps/core/persian.py` and handles Arabic ي/ك, NBSP, ZWNJ and whitespace, but it is only applied to incoming query strings in `ItemFilterSpec.from_dict`. Stored values are never normalized, so a product saved with Arabic ي is invisible to a search typed with Persian ی — the normalization currently protects only one side of the comparison.

**Implementation.** Controlled vocabularies for stone type, colour and processing type, following the `Application` pattern from migration `0007` — a platform-wide table with `code`, `name`, `sort_order`, `is_active`, seeded and backfilled by normalized name match. Quarry/region stays extensible free text with normalization and autocomplete, because Iranian quarry names are numerous and a closed list would be wrong. Grade/sort stays free text for the same reason: the industry does not use one vocabulary.

Normalization is applied on write in the inventory services and retrofitted by a data migration.

**Explicitly excluded from normalization:** `Trade`, `TradeItem` and `SalesInvoiceItem` snapshot columns. Those are historical commercial facts, and rewriting them would silently alter documents that have already been sent to customers.

**Tests.** A product stored with Arabic variants is found by a query typed with Persian ones, and the reverse.

---

# P2-06 — Branch protection

Documented rather than applied, unless the authenticated GitHub tooling can do it safely. Required checks: lint and system checks, SQLite tests, PostgreSQL tests, concurrency tests, migrations from zero, deploy checks, and the dependency audit once it is blocking. Pull request required before merge; no direct pushes to `master`.

---

# SMS gateway

**Confirmed.** `PROVIDERS` in `apps/accounts/sms.py` contains only `console` and `null`, both with `delivers = False`. Production already refuses to boot on a non-delivering provider unless `SMS_ALLOW_UNDELIVERED=true`, which is correct fail-closed behaviour and means SANGA cannot currently launch.

**Implementation.** A Kavenegar adapter on the existing `SmsProvider` interface, using its transactional OTP endpoint, with credentials from the environment and an explicit timeout. It is built on stdlib `urllib.request` rather than adding an HTTP dependency, since one request to one endpoint does not justify one.

The API key travels in the URL path, so the URL is never logged; neither are OTP codes. Vendor failures raise a controlled error. Configuration is validated at production import so a missing key fails at boot rather than at the first login attempt. Tests are mock-based and make no network calls.

Staff login and customer verification keep their separate OTP purposes and share the delivery abstraction.

---

# Verification approach

There is no Docker and no PostgreSQL on the development machine, and the concurrency tests skip themselves on SQLite by design. The PostgreSQL lane is therefore verified in GitHub Actions, whose `postgres` job runs `postgres:16` and already fails when concurrency tests are collected but skipped.

Run locally: `ruff check .`, `manage.py check`, `makemigrations --check --dry-run`, `pytest`, fresh SQLite migrate, `check --deploy --fail-level WARNING`, `pip-audit`.
Run in CI: the full PostgreSQL suite, the concurrency suite, migrations from zero on both engines, deploy checks and the dependency audit.

CI results will be reported as CI results. No claim that the PostgreSQL suite passed will be made before the job is green.
