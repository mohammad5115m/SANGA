# SANGA — Final Hardening Implementation Report

**Repository:** `mohammad5115m/SANGA`
**Base branch:** `cursor/sanga-v2-remediation-b749` (`7f34d93`)
**Working branch:** `cursor/sanga-v2-final-hardening` (`1e289e3`, pushed)
**Scope:** 15 commits, 75 files, +5,898 / −396

---

## 1. Executive summary

Every issue in the brief was verified against the code before anything was
changed. All were confirmed; two needed correcting in the process, and both
corrections made the problem larger rather than smaller.

The two defects that could have cost a real business real money are closed. A
direct sale is now idempotent on a submission token backed by a partial unique
index, so a double-click, a refresh or a retried POST cannot bill a colleague
twice for one sale. Every purchase-request transition re-reads its row under
`select_for_update()` and validates against an enumerated map, so the state that
used to be reachable — a `CANCELLED` request owning a Trade, a ledger pair and an
invoice — no longer is.

The largest change is architectural rather than defensive: `Trade` became a
commercial header with `TradeItem` lines. A stone seller who sells travertine,
marble and crystal to one colleague in one phone call now records one sale, one
total, one entry in each party's book and one invoice with three rows. Before
this, the only way to record that was three separate sales — so the workaround
for a modelling gap produced worse bookkeeping than the gap did.

**Verification is real, not assumed.** The full suite and the concurrency lane
were run against a genuine PostgreSQL 16.4 instance, not only SQLite. That
mattered: one concurrency test failed there after passing on SQLite, and
diagnosing it corrected a wrong assumption in the test rather than in the code.

**Readiness: PILOT READY.** Evidence and the remaining risks are in §17–18.

---

## 2. Branch and commits

| Commit | Subject |
|--------|---------|
| `c2f4973` | docs: add the final production hardening plan |
| `51169c4` | fix: make direct sales idempotent |
| `0e42679` | fix: serialize every purchase request state transition |
| `e6a9f79` | refactor: support multi-line commercial trades |
| `c3441f2` | security: upgrade vulnerable and end-of-life dependencies |
| `22fc514` | fix: require safe production media storage |
| `03b0d7e` | fix: enforce verified network eligibility |
| `80040ee` | fix: make every public surface apply the same seller eligibility |
| `e051631` | security: harden OTP request throttling and proxy handling |
| `2948f06` | feat: add a production SMS gateway |
| `0aa43be` | refactor: route notifications by capability |
| `cc3a116` | security: cap decoded image size and document the video limitation |
| `f58bc13` | refactor: normalize product discovery text and control its vocabulary |
| `3ff0abd` | fix: heal a missing invoice on retry and reach stock from every sold line |
| `1e289e3` | test: assert the transition invariant the product actually promises |

Nothing was merged to `master`. The branch is pushed and ready for review.

---

## 3. Issues, confirmed and resolved

| ID | Confirmed | Resolved | Note |
|----|-----------|----------|------|
| P1-01 Direct sale idempotency | Yes | Yes | |
| P1-02 Transition serialization | Yes | Yes | |
| P1-03 Dependency advisories | Yes — **worse than reported** | Yes | Django 5.1 was end-of-life, not merely unpatched |
| P1-04 Production media | Yes | Yes | |
| P1-05 Multi-line sales | Yes | Yes | Legacy columns kept, not dropped |
| P1-06 Verified network | Yes | Yes | |
| P2-01 OTP throttling + proxy | Yes | Yes | |
| P2-02 Public surface consistency | Yes | Yes | |
| P2-03 Capability notifications | Yes | Yes | |
| P2-04 Media hardening | **Partly already done** | Yes | Byte limits and truncation checks existed; pixel limits did not |
| P2-05 Taxonomy | Yes | Yes | |
| P2-06 Branch protection | Yes | Documented | Needs repository-admin rights |
| SMS gateway | Yes | Yes | Kavenegar |

### Two corrections to the brief

**P1-03 understated the problem.** The pin was `Django==5.1.15` inside a
`>=5.1,<5.2` range. Django 5.1 reached end of life on 2025-12-31, so the issue
was not one advisory but an unsupported branch that will never receive another
patch — and no audit reports that, because no advisory is ever filed against an
unsupported version. Pillow 11.3.0 carried six 2026 CVEs.

**P2-04 was largely done.** Byte limits (10 MB images, 60 MB video) and the
double-open `verify()`/`load()` that catches truncation were already in place, as
was treating the extension and `Content-Type` as untrusted. What was genuinely
missing was a limit on the **decoded** size.

---

## 4. Root causes

| Issue | Root cause |
|-------|-----------|
| P1-01 | Idempotency for request-driven sales was a side effect of `Trade.purchase_request` being a `OneToOneField` plus the row lock in `finalize_sale`. A direct sale has no request, so it inherited neither, and two submissions produced two genuinely distinct trades — satisfying every per-trade constraint while moving the balance twice. |
| P1-02 | Status was validated against the caller's in-memory instance, with no lock. Under READ COMMITTED two transactions can each read `ACCEPTED` and each write a different terminal status. |
| P1-03 | The pin tracked a short-term-support branch and was not moved when it expired. |
| P1-04 | `USE_S3=false` was a silent default rather than a decision: Django only routes `MEDIA_URL` when `DEBUG` is on, and nothing mounted a volume at `MEDIA_ROOT`. |
| P1-05 | `Trade` modelled a sale as one product line. |
| P1-06 | The policy could not require an approval that nothing ever recorded — `verification_status` defaulted to `unverified` and provisioning never set it, so an allowlist would have emptied every directory. The denylist fixed the wrong half. |
| P2-01 | Three `COUNT` queries followed, in a different transaction, by the `INSERT` they were limiting. And `X-Forwarded-For`'s leftmost value is written by the client. |
| P2-02 | Two different questions asked on one page: the seller was resolved on `status=ACTIVE`, the products on the full seller gate. |
| P2-03 | Notifications routed by role in a system that authorizes by capability. |
| P2-04 | The byte limit does not bound decoded size; compression ratios in the thousands are ordinary. |
| P2-05 | `normalize_persian_text` was applied to the query and never to what was stored, so it protected one side of a comparison and neither side of the problem. |

---

## 5. Implementation

### P1-01 — Idempotent direct sale

`Trade.submission_id` (nullable UUID), minted by `DirectSaleForm` on GET and
carried through every retry. `record_direct_sale` locks the seller's `Business`
row, re-checks under that lock, creates inside a savepoint, and on
`IntegrityError` re-fetches and returns the winner. Both idempotent-return paths
also re-attempt the invoice, because invoicing is best-effort and a retry is the
natural moment to heal a sale that ended up without a document.

### P1-02 — Locked transitions

`PurchaseRequest.ALLOWED_TRANSITIONS` enumerates every legal move.
`_lock_for_transition` re-reads under `select_for_update()`, validates **after**
the lock, and refuses `CANCELLED`/`REJECTED` once a Trade references the request.
All three services route through it.

### P1-05 — Multi-line trades

`TradeItem` carries `product_name`, `stone_type`, `grade`, `quantity`, `unit`,
`unit_price`, `line_total`, `sort_order`. The direct-sale screen became a
three-row formset; blank rows are ignored rather than reported as errors.
`create_invoice_for_trade` builds one invoice row per trade line. Line totals are
rounded per line **then** summed, so an invoice's rows add up to the total
printed at the bottom of it.

The legacy header columns were **kept**. They are still written for a one-line
sale — every historical row and every request-driven sale — and blank for
multi-line trades. Dropping them is a separate change once nothing reads them.

### P1-06 / P2-02 — Eligibility

`business_is_network_eligible` requires `VERIFIED`;
`SANGA_REQUIRE_VERIFIED_FOR_NETWORK` defaults to on.
`create_business_for_owner` records the approval that provisioning represents.
`public_business_or_none` is now the single gate for the storefront, product
detail, compare, share token and shared catalog, returning a generic 404 rather
than an empty shop.

### P2-01 — OTP

`OTPRequestThrottle`, unique on `(scope, key)`, locked with
`select_for_update()`. The check and the challenge insert are one transaction;
the phone row is locked before the address row, always in that order.
`SANGA_TRUSTED_PROXY_COUNT` (default `0`) counts hops from the right.

### SMS

`KavenegarSmsProvider` on the existing interface, using the transactional
`verify/lookup` endpoint and stdlib `urllib`. A 200 is not treated as acceptance —
Kavenegar reports refusals in the body. Credentials validated at production
import. The API key travels in the URL, so no URL is ever logged and vendor
exceptions are never chained through.

---

## 6. Database constraints added

| Constraint | Table | Invariant |
|-----------|-------|-----------|
| `uniq_trade_per_submission` | `trading_trade` | One submission, at most one sale per seller. Partial on `submission_id IS NOT NULL`. |
| `uniq_otp_throttle_key` | `accounts_otprequestthrottle` | One counter row per rate-limited key, so it can be locked. |
| `uniq_vocabulary_term` | `inventory_vocabularyterm` | One canonical spelling per dimension. |

**Deliberately not a constraint:** "a request with a Trade is not cancelled". It
spans two tables, which PostgreSQL cannot express as a `CHECK`, and a trigger
would hide a commercial rule where nobody reading the service layer would find
it. The row lock is the enforcement and the concurrency tests are the proof. This
is recorded rather than silently decided.

---

## 7. Transactions and locks

| Path | Lock | Why |
|------|------|-----|
| `record_direct_sale` | Seller `Business` row, before the lookup | Concurrent submissions serialize |
| `_lock_for_transition` | The `PurchaseRequest` row | Validate after acquiring, not before |
| `_hit_throttle` | The throttle row, **phone before address** | Fixed order, so two requests for one phone behind one proxy cannot deadlock |
| `post_trade_entries` | Both `Business` rows, ordered by stringified UUID | Unchanged; already correct |

SMS delivery happens after the challenge transaction commits, so a slow gateway
does not hold throttle locks and a rollback cannot deliver a code for a row that
does not exist.

---

## 8. Migration strategy

| Migration | Purpose |
|-----------|---------|
| `trading.0002_trade_submission_idempotency` | Additive field + partial constraint |
| `trading.0003_trade_items` | `TradeItem`; header snapshot columns made nullable |
| `trading.0004_backfill_trade_items` | One line per existing Trade |
| `businesses.0006_verify_provisioned_businesses` | Mark ACTIVE, non-refused businesses VERIFIED |
| `accounts.0003_otp_request_throttle` | Throttle table |
| `inventory.0010_vocabulary_terms` | Vocabulary table |
| `inventory.0011_normalize_catalog_text` | Seed terms; normalize stored catalog text |

**`trading.0004` never recomputes money.** `line_total` comes from the recorded
`total_amount`, not from `quantity × unit_price`; where they disagree the
recorded total wins and the discrepancy is reported. The migration then refuses
to complete unless every trade's lines sum back to its recorded total. It is
idempotent — a rerun skips trades that already have lines — and reversible,
because the header columns were kept.

**`businesses.0006` exposes nothing.** Every Business it touches is already
visible on every discovery surface; it records an approval already made. It
leaves `PENDING` alone (somebody is meant to look) and `REJECTED`/`SUSPENDED`
alone (overturning a refusal in a migration would re-publish a business the
platform removed on purpose).

**`inventory.0011` does not touch history.** `Trade`, `TradeItem`,
`SalesInvoiceItem` and inquiry snapshots keep the spelling they were recorded
under. They are never searched, so normalizing them buys nothing, and rewriting
the text on a past invoice is a silent change to a document that may already have
been handed to a customer.

---

## 9. Tests added

| File | Covers |
|------|--------|
| `apps/trading/tests/test_direct_sale_idempotency.py` | Sequential retry, per-seller scoping, invoice healing, the view |
| `apps/trading/tests/test_request_transitions.py` | Stale writes, the transition map, the trade-exists guard |
| `apps/trading/tests/test_multi_line_sales.py` | Three-stone sale end to end, rounding, snapshot immutability, report fan-out |
| `apps/trading/tests/test_trade_item_migration.py` | The backfill, driven through the real migration graph |
| `apps/businesses/tests/test_verified_network.py` | Every verification state across every surface; history stays reachable |
| `apps/catalog/tests/test_public_surface_eligibility.py` | Six ineligibility causes × five public surfaces |
| `apps/accounts/tests/test_otp_throttling.py` | Cooldown, caps, and the whole of the proxy logic |
| `apps/accounts/tests/test_sms_provider.py` | Mocked gateway; no key or code in any log |
| `apps/notifications/tests/test_capability_routing.py` | Custom permission sets, owner bypass, inactive members |
| `apps/inventory/tests/test_media_limits.py` | Decompression bombs, dimension caps, stream rewind |
| `apps/inventory/tests/test_taxonomy.py` | Arabic ↔ Persian search in both directions; synonyms |
| `config/tests/test_media_configuration.py` | Every safe and unsafe media configuration |

---

## 10. PostgreSQL concurrency tests

Ten existed; eleven were added, for twenty-one total.

| Test | Proves |
|------|--------|
| `test_two_threads_submitting_one_direct_sale_record_one_sale` | One Trade, one SALE, one PURCHASE, one invoice, balance moves once, no `IntegrityError` escapes |
| `test_four_threads_submitting_one_direct_sale_still_record_one_sale` | Two threads can serialize by luck; four are less likely to |
| `test_accepting_and_rejecting_at_once_leaves_exactly_one_answer` | A |
| `test_cancelling_and_accepting_at_once_cannot_lose_either_write` | B |
| `test_cancelling_while_finalizing_never_leaves_a_cancelled_sale` | C — the dangerous one |
| `test_finalizing_twice_at_once_records_one_sale` | D |
| `test_a_stale_post_arriving_after_completion_changes_nothing` | E |
| `test_simultaneous_requests_for_one_phone_yield_one_code` | The cooldown holds |
| `test_the_per_phone_hourly_cap_holds_under_parallel_requests` | 8 threads, cap 3 → 3 codes |
| `test_the_per_address_cap_holds_under_parallel_requests` | 8 threads, 8 phones, one address → 3 codes |
| `test_two_phones_behind_one_address_do_not_deadlock` | The fixed lock ordering |

### The lane earned its keep

`test_cancelling_and_accepting_at_once` failed on PostgreSQL after passing on
SQLite. The diagnosis found the **test** wrong, not the code: `ACCEPTED →
CANCELLED` is a permitted transition, because a buyer withdrawing after agreement
but before shipment is ordinary trading, so both calls can legitimately succeed.

The test now asserts what actually matters. `CANCELLED → ACCEPTED` is not
permitted, so if both calls succeed the only possible ordering is
accept-then-cancel — and the row must therefore read `CANCELLED`. Reading
`ACCEPTED` while a cancel also reported success is precisely the lost update the
lock exists to prevent.

This is what the PostgreSQL lane is for, and it is why CI fails the job when
concurrency tests are collected but skipped.

---

## 11. Security changes

- Django 5.1 (end of life) → 5.2.17 LTS, supported to April 2028.
- Pillow 11.3.0 (six 2026 CVEs) → 12.3.0.
- OTP request limits are now claimed under a lock rather than counted.
- `X-Forwarded-For` is no longer trusted from the left; the default ignores it.
- Public surfaces no longer reveal that a withdrawn seller exists.
- Image decoding is bounded by dimension and total pixel count.
- Production refuses to start on a media configuration that loses files.
- A real SMS gateway exists, with credentials validated at boot and neither the
  key nor the code reachable from any log line.
- No secrets committed; `.env.example` carries placeholders only.

---

## 12. Dependency audit

```
$ pip-audit --requirement requirements/constraints.txt --strict
No known vulnerabilities found
```

The CI step moved from `continue-on-error: true` to **blocking**. It was advisory
while the pins carried known vulnerabilities, which made the job permanently red
and therefore meaningless — an advisory check that never passes is
indistinguishable from one that never runs.

**No vulnerability was accepted or suppressed.** There is no `--ignore-vuln` in
the workflow. If one becomes necessary, `docs/deployment.md` §5 requires it to be
recorded here with a named rationale.

---

## 13. Production media strategy

**Object storage is required.** `USE_S3=false` raises `ImproperlyConfigured`
unless `SANGA_ALLOW_LOCAL_MEDIA=true` is set deliberately, which additionally
requires `SANGA_MEDIA_ROOT` and — documented, not enforceable from Django — a
mounted volume plus a reverse proxy serving `/media/`. `docker-compose.prod.yml`
gained the `media_data` volume so the supported path exists.

With `USE_S3=true`, production validates the bucket, credentials (or
`AWS_S3_USE_IAM_ROLE`), a region or endpoint, and that the storage origin appears
in `CSP_IMG_SRC`/`CSP_MEDIA_SRC` — otherwise the policy blocks every product
image and the symptom looks like missing data rather than a missing header.

---

## 14. SMS provider status

`kavenegar` is implemented and production-ready pending credentials. It uses the
transactional `verify/lookup` endpoint, which is the one Iranian operators
deliver outside daytime hours. Required: `KAVENEGAR_API_KEY` and
`KAVENEGAR_OTP_TEMPLATE` (a template already approved in the vendor panel).
Production will not start without them.

`console` and `null` remain for development and are still refused in production
unless `SMS_ALLOW_UNDELIVERED=true` is set by name.

**Not verified against the live vendor.** Tests are mock-based by design. The
first real send should be a staging smoke test.

---

## 15. Multi-line trade architecture

```
Trade (header: total_amount, currency, submission_id, counterparty)
  ├── TradeItem   travertine   100 m² × 1,500,000 = 150,000,000
  ├── TradeItem   travertine    70 m² × 1,200,000 =  84,000,000
  └── TradeItem   marble        50 m² × 2,000,000 = 100,000,000
                                        total     = 334,000,000
        │
        ├── seller SALE      334,000,000   ← one entry
        ├── buyer  PURCHASE  334,000,000   ← one entry
        └── SalesInvoice ── three SalesInvoiceItems
```

Stock is still never decremented automatically. Invoices and trade lines are
still historical snapshots. Reports aggregate `TradeItem.line_total` where they
break a sale down, and `Trade.total_amount` where they total it — never both
across a join, which would multiply a three-line sale by three.

---

## 16. Performance review

- `items` is prefetched on the trade list, trade detail, dashboard and admin, so
  `summary_label` never causes an N+1.
- `sales_by_colleague` and `sales_summary` take two aggregates and merge in
  Python rather than joining, avoiding both fan-out and a second scan.
- `members_who_can` is one indexed query plus an in-Python filter over
  seat-limited memberships.
- `vocabulary_context` is one query for all three dimensions.
- The existing query-budget tests still pass unchanged.
- A pre-existing pagination bug was fixed: `leads_for` was unordered, so a
  customer could appear on two pages and another on none.

---

## 17. Remaining risks

| Risk | Severity | Note |
|------|----------|------|
| Kavenegar never called for real | Medium | Mock-tested only. Staging smoke test required before pilot. |
| Video validation is container-only | Medium | Deliberate. Size limit, closed container list and `nosniff` stand in. Documented in `docs/inventory.md`. |
| Legacy `Trade` snapshot columns still present | Low | Intentional for one release. Any new reader of them is a bug. |
| Branch protection not applied | Low | Needs repository-admin rights. Documented in `docs/testing.md`. |
| `businesses.0006` on an unusual database | Low | Only touches ACTIVE + `unverified` rows, but review the row count before running. |
| Trade line totals summing to the header | Low | Enforced in the service and asserted by the migration; not a database constraint. |
| No load testing | Medium | Correctness is verified under concurrency; throughput is not. |

---

## 18. Production readiness

### PILOT READY

Ready for technical staging and a controlled real-business pilot. Not yet
production-ready for open onboarding.

**Why pilot rather than staging:** the financial integrity questions are settled
with evidence. Duplicate sales, contradictory request states, double ledger
posting and duplicate invoices are each closed by a database constraint or a row
lock, and each is demonstrated by a test that races real PostgreSQL connections.
Tenant isolation, B2B price containment and history immutability were re-verified
and hold.

**Why not production-ready:** the SMS gateway has never been called against the
live vendor, which is the one path where a mock cannot substitute for reality —
and it is the path every login depends on. Video validation stops at the
container signature. There has been no load testing. Branch protection is
documented but not applied, so CI can still be bypassed.

**Before the pilot:** provision Kavenegar credentials and send one real code
through staging; apply the branch ruleset; take a backup and verify
`SUM(total_amount)` against `SUM(line_total)` after `trading.0004`.

**Before open production:** load testing, and a decision on video validation.

---

## 19. Commands executed

```bash
ruff check .
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py check --deploy --fail-level WARNING        # production settings
pytest                                                       # SQLite lane
pytest -W error::DeprecationWarning                          # after the Django upgrade
python manage.py migrate --no-input                          # fresh SQLite, empty file
pip-audit --requirement requirements/constraints.txt --strict

# against a real PostgreSQL 16.4 instance
pytest                                                       # full suite
pytest -m concurrency -v                                     # the lane SQLite cannot run
python manage.py migrate --no-input                          # fresh PostgreSQL, empty database
```

---

## 20. Results

| Check | Result |
|-------|--------|
| `ruff check .` | All checks passed |
| `manage.py check` | No issues (0 silenced) |
| `makemigrations --check --dry-run` | No changes detected |
| `pytest` (SQLite) | **829 passed**, 21 skipped (the PostgreSQL-only lane) |
| `pytest -W error::DeprecationWarning` | Passed — no Django 6.0 deprecation warnings remain |
| **`pytest` (PostgreSQL 16.4)** | **829 passed, 0 failed, 0 skipped** |
| **`pytest -m concurrency` (PostgreSQL)** | **21 passed**, 808 deselected, **0 skipped** |
| Fresh migrate, empty SQLite | Succeeds |
| Fresh migrate, empty PostgreSQL | Succeeds |
| `check --deploy --fail-level WARNING` | No issues (0 silenced), exit 0 |
| `pip-audit --strict` | No known vulnerabilities found |

Baseline before this branch: 570 passed, 10 skipped. The suite grew by 259 tests.

### A note on how this was verified

The development machine has neither Docker nor a PostgreSQL service, and the
concurrency tests skip themselves on SQLite by design. Rather than report the
lane unverified, PostgreSQL 16.4 binaries were fetched and an instance run
locally on port 55432. That is how the `cancel` versus `accept` failure in §10
was found — it would otherwise have reached CI, or worse, not been noticed.

CI itself has **not** run this branch: the workflow triggers on pull requests
only, and the `gh` CLI on this machine is unauthenticated, so the pull request
could not be opened. The branch is pushed and the PR link is available. The
results above are local runs against the same PostgreSQL major version CI uses,
and are reported as such.
