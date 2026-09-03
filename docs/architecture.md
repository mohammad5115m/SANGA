# Architecture — سنگا (SANGA)

## 1. Architectural style

**Django modular monolith** with domain apps, a service/selector layer, Django
templates, hand-written RTL CSS and a little Alpine for disclosure widgets.

Not microservices, not React, not Elasticsearch. One deployable unit is the right
size for this team and this product, and none of the alternatives solves a
constraint that actually exists here. PostgreSQL is the search engine.

The layering is consistent across every app:

```text
models.py      structure and invariants that belong in the database
policy.py      who may see what          (inventory only, deliberately)
filters.py     one serializable filter schema
selectors.py   reads, tenant-scoped, prefetch-aware
services.py    writes, transactional, permission- and plan-gated
forms.py       validation and widgets
views.py       HTTP glue, no business rules
```

**Business rules live in services, never in views or templates.** A rule enforced
by hiding a button is not enforced.

## 2. App structure

```text
config/           settings, urls, asgi/wsgi, celery
apps/
  core/           shared utilities, base templates, middleware, health, seed, test builders
  accounts/       User, OTP (staff login and customer verification, separate purposes)
  businesses/     Business, Membership, plan entitlements, colleague directory, dashboard
  inventory/      Product, InventoryLot, media, freshness, eligibility policy, filter schema
  pricing/        PriceTier, LotPrice — two audience channels with validity windows
  marketplace/    colleague browse and search (no models of its own)
  catalog/        storefront, public search, catalogs, share links, public inquiry flow
  inquiries/      CustomerLead, Inquiry, InquiryItem, seller inbox
  trading/        product-bound PurchaseRequest, finalized Trade
  invoicing/      SalesInvoice, SalesInvoiceItem
  accounting/     immutable LedgerEntry, aging
  reporting/      operational reports (no models)
  notifications/  in-app notifications

  contacts/       RETIRED — model kept for pre-V2 ledger rows, no UI
  purchase_requests/ RETIRED — demand board, rows kept read-only
  partners/ matching/ reservations/  migration history only, no models
```

The retired apps stay installed because removing them breaks `migrate` on an
empty database. See [v2-migration-strategy.md](./v2-migration-strategy.md) §4.2.

## 3. The two keystones

Both exist because the same question was being answered in several places, and
the copies had drifted.

**`inventory/policy.py`** — `eligible_items(audience, viewer_business)` is the
only definition of a buyer-visible product. Marketplace, public search,
storefront, share links and catalogs all go through it. Before it existed, the
catalog path checked `status` but forgot `visibility`, and a private product
attached to a share link rendered publicly with its price.

**`inventory/filters.py`** — `ItemFilterSpec` is one serializable filter
vocabulary shared by «موجودی من», the marketplace and public search. Catalog
creation can use the same spec for a short-lived "select all matches" action,
then stores only explicit item membership.

## 5. Multi-Tenancy Model

**Tenant = Business.**

- Every tenant-sensitive row carries `business_id` (or is reachable via a FK chain that is always joined in selectors).
- Membership (`BusinessMembership`) defines role + permission grants.
- Selectors must always accept `business` / `actor` context.
- Cross-tenant access tests are mandatory.

## 6. Auth Architecture

- Custom user model with phone as primary identifier.
- OTP login abstracted behind `SmsProvider` interface.
- Dev provider writes OTP to console/DB (never blocks developers).
- Rate limiting + attempt tracking on OTP endpoints.
- Session/device management planned; sessions first, devices later if needed.

## 7. Pricing architecture (a security boundary)

```text
InventoryLot
    └── LotPrice (tier=b2b|b2c, mode, amount, validity window, special sale)
```

Two channels, and only two. `ContactPrice` — a third, per-counterparty axis — was
removed in V2.

Access path:

1. Resolve the **audience** (`owner_staff`, `b2b_partner`, `b2c_public`,
   `platform_admin`).
2. The query layer prefetches **only that audience's tier**, so a B2B row is
   never loaded in memory on a public page.
3. `pricing.services.resolve_visible_prices()` filters by audience again, and a
   disallowed tier is *absent* from the result rather than blanked.
4. Templates receive a flat, pre-resolved dict with no tier map to walk.

Special-sale pricing lives on the tier row, not on the item, so it inherits that
protection instead of being an unlabelled number outside it.

Details: [permissions.md](./permissions.md), [pricing.md](./pricing.md).

## 8. Search architecture

PostgreSQL, through the ORM. There is no search service abstraction and no
external engine, because nothing has yet needed one.

Everything goes through `inventory.filters.ItemFilterSpec`, so adding an engine
later means implementing one `apply()`, not rewriting four screens.

Persian normalization (ی/ي، ک/ك، ZWNJ, whitespace) lives in
`apps.core.persian` and is applied when a filter spec is built.

## 9. Media architecture

- `LotMedia` — any number of images and videos per product, with `sort_order`,
  `is_primary` and `caption`.
- Local storage in development, S3-compatible in production via django-storages.
- Uploads validated on extension **and** content type, with size ceilings. The
  browser-supplied `Content-Type` is a hint only: it is attacker-controlled.
- No transcoding pipeline, no watermarking.

## 10. Async

Celery and Redis are wired up and `CELERY_BEAT_SCHEDULE` is **empty**.

Both v1 periodic jobs are gone. The hourly freshness sweep mutated `status` on
every row to express something derivable from two timestamps; the half-hourly
saved-search matcher belonged to a feature that was removed. Notifications are
created synchronously with a direct ORM write.

The wiring is kept because an idle broker costs nothing and re-introducing the
plumbing later would be disruptive. Deriving state at read time is what made the
jobs unnecessary — that is the design point, not the absence of Celery.

## 11. Frontend architecture

- Django templates, server-rendered. HTMX is loaded and used as a global
  `hx-boost`; there are no partial-swap fragments.
- Alpine handles local UI state only — filter disclosure, gallery selection,
  copy-to-clipboard. No state that matters lives in the browser.
- Hand-written RTL CSS tokens and components in `static/css/app.css`. No Node,
  no Tailwind, no build step.
- Money is rendered through the `rial` template filter rather than
  `humanize.intcomma`, which returns numbers *ungrouped* under the `fa` locale.
  Nine-digit Rial amounts without separators are how somebody misreads a price
  by a factor of ten.
- PWA: installable with an offline fallback page. Prices and stock are
  deliberately not cached aggressively.

## 12. Deployment Architecture (Target)

```text
Browser / PWA
    │
    ▼
Nginx (TLS, static, media proxy)
    │
    ▼
Gunicorn (Django)  ── Redis ── Celery worker/beat
    │
    ▼
PostgreSQL
    │
    ▼
S3-compatible object storage (prod media)
```

Docker Compose provides local parity: `web`, `db`, `redis`, `worker`, `beat`.

## 13. Observability & Ops

- Structured logging
- `/health/` liveness + DB/redis checks
- Environment-based settings (`base`, `development`, `production`)
- Secrets only via env
- Backup guide for PostgreSQL + media

## 14. Testing strategy

| Layer | Focus |
|-------|-------|
| Unit | price resolution and expiry, stock freshness, balance math, FIFO aging, filter spec round-trip |
| Integration | provisioning, product creation, request → accept → finalize → ledger → invoice, public multi-product inquiry |
| Authorization | tenant isolation, B2B non-leakage on every public surface, plan gates at the service layer |
| Invariants | `apps/core/tests/test_security_invariants.py` — the rules that would cause real harm if relaxed |
| Query budgets | `apps/core/tests/test_query_budgets.py` — flat in the number of rows, so an N+1 fails immediately |

Two things are worth knowing about the suite:

- `apps/core/testing.py` holds shared builders whose defaults produce a
  *sellable, publicly visible* product. Tests that care about a lifecycle state
  set it explicitly, which keeps the interesting part of each test visible.
- The security invariants are duplicated from the feature suites on purpose. A
  single failing file named "security invariants" is a clearer signal than one
  failure buried three apps away.

## 15. Major risks and mitigations

| Risk | Mitigation |
|------|------------|
| B2B price leaking publicly | Tier-scoped prefetch **and** audience filter, plus a test across every public surface |
| Visibility rules drifting apart | One `eligible_items()`; there is nowhere else to put the question |
| Duplicate ledger entries | Row lock + pre-check + partial unique constraint |
| Stale stock presented as current | Freshness derived at read time; expired quantities degrade to «استعلام موجودی» |
| Capability rename revoking access | Materialized permission lists, so every change ships a data migration |
| N+1 queries | Query-budget tests that are flat in row count |
| Over-abstraction | Services only where rules exist; `marketplace` and `reporting` own no models |

## 16. Key ADRs

See `docs/decisions/`:

- ADR-0001 Django-first modular monolith  
- ADR-0002 Pricing isolation app  
- ADR-0003 OTP auth with provider abstraction  
- ADR-0004 PostgreSQL search first  
