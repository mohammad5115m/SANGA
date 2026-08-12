# Architecture — سنگا (SANGA)

## 1. Repository Assessment (Phase 0)

**Inspection result:** The workspace was empty (no existing Django app, no dependencies, no git history available in PATH).

Therefore we bootstrap a greenfield Django-first monorepo with documentation-first Phase 0, then Phase 1 foundation.

## 2. Architectural Style

**Django modular monolith** with clear domain apps, service/selector layers, and HTMX (+ Alpine where useful) UI.

### Why not microservices / React / Next.js?

- Small product team needs one deployable unit.
- Excellent UX is achievable with Django Templates + HTMX + hand-written RTL CSS tokens (`static/css/app.css`). Tailwind was considered and deferred — there is no Node/Tailwind build step.
- React would increase complexity without solving a proven constraint.
- Search starts with PostgreSQL; Meilisearch/ES can plug into a search service later (ADR-0004). The `SearchService` interface itself is **not implemented yet**.

## 3. Recommended App Structure

### Built today

```text
config/                       # project package (settings, urls, asgi/wsgi, celery)
apps/
  core/                       # shared utilities, base templates, middleware, health, seed
  accounts/                   # User, OTP auth
  businesses/                 # Business, Membership, Warehouse, onboarding, dashboard
  inventory/                  # Product, InventoryLot, media, freshness, status
  pricing/                    # PriceTier, LotPrice, ContactPrice, audience-aware resolution
  catalog/                    # public storefront, custom catalogs, sharing previews
  marketplace/                # colleague browse/search + saved searches
  purchase_requests/          # PR board + private offers
  inquiries/                  # inquiry pipeline
  contacts/                   # lightweight CRM (private per-business contacts)
  accounting/                 # per-contact ledger + manual trade recording
  notifications/              # in-app notifications (no preference table yet)
design/                       # design tokens, component notes
docs/                         # product & engineering docs
templates/                    # global templates
static/                       # hand-written CSS + assets
```

### Planned / not built as apps

`analytics/`, `audit/`, `moderation/`, and `platform_admin/` are **roadmap items**,
not packages in the tree. Capability codes like `analytics.view` / `audit.view`
exist as reserved strings; they gate nothing yet. Platform ops use Django Admin.

### Why this structure (vs dumping everything into `inventory`)?

- **Pricing is isolated** so B2B leakage becomes a hard architectural boundary.
- **Catalog vs marketplace** separate public B2C UX from private B2B UX.
- **Notifications** stay cross-cutting; analytics/audit/moderation stay deferred.

There is no live `partners/` product surface: membership of the network is having
an account, so lot visibility alone decides who sees what, and `marketplace/` owns
the B2B experience (including `SavedSearch`). The `matching/` and `reservations/`
features were removed with the models they served. All three package names remain
in `INSTALLED_APPS` as empty, migrations-only stubs until the history is squashed.

## 4. Layering Conventions

Each domain app should prefer:

```text
models/          # split by entity when large
selectors.py     # read queries (tenant-scoped)
services.py      # write workflows / business rules
permissions.py   # object-level checks
forms.py         # Django forms
views/           # HTTP adapters only
templates/       # presentation
tests/
```

Rules:

- No critical business logic in templates.
- No permission logic duplicated ad hoc in every view — use reusable checks/mixins/decorators.
- Avoid fat signals; prefer explicit service calls.
- Avoid circular imports via selectors/services and careful app boundaries.

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

## 7. Pricing Architecture (Security Boundary)

```text
InventoryLot
    ├── LotPrice (tier=B2B|B2C, amount, currency, unit)
    └── ContactPrice (contact, amount, currency, unit)  # optional override
```

Access path:

1. Resolve **audience** (`owner_staff`, `b2b_partner`, `b2c_public`, `platform_admin`).
2. Ask `pricing.services.resolve_prices_for_viewer(lot, audience, viewer_business=…)`.
3. Serializers/templates receive only allowed price fields (including a `"contact"`
   key only when the viewer is the linked colleague).
4. Public catalog endpoints never join/select B2B amounts or contact overrides.

Future tiers = new `PriceTier` rows + policy mapping — not a rewrite.

Details: [permissions.md](./permissions.md), [pricing.md](./pricing.md).

## 8. Search Architecture

**Planned** (ADR-0004), not a shipped abstraction yet:

```text
SearchService interface          # not implemented
  └── PostgresSearchService (v1)
  └── (future) MeilisearchService
```

Today, list/search screens use ordinary Django ORM filters/annotations. Persian
normalization (ی/ي، ک/ك، whitespace, ZWNJ) should live in a shared normalizer when
the search service lands.

## 9. Media Architecture

- `LotMedia` with type, order, primary flag, captions.
- Local storage in development; S3-compatible in production via django-storages abstraction.
- Thumbnails generated asynchronously (Celery).
- Optional watermarking hook later; do not block v1.

## 10. Async / Realtime

| Need | Tool |
|------|------|
| Freshness evaluation, thumbnails, notifications | Celery + Redis |
| Realtime chat / live bidding | **Not in v1** (Channels only if later justified) |

## 11. Frontend Architecture

- Django Templates + HTMX for progressive enhancement
- Alpine.js is loaded and available for local UI state; most screens use plain
  server-rendered forms/HTMX today
- Hand-written RTL CSS tokens and components in `static/css/app.css` (no Tailwind
  build pipeline)
- PWA: installable, offline fallback page, **no aggressive caching of prices/stock**
  (hardening target; not fully verified)

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

## 14. Testing Strategy

| Layer | Focus |
|-------|-------|
| Unit | pricing resolution (incl. ContactPrice), freshness, balance math |
| Integration | onboarding, quick-add, demand → offer → recorded trade |
| Authorization | tenant isolation, B2B leakage, visibility matrix, network privacy |
| E2E (later) | critical happy paths with Playwright if practical |

## 15. Major Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Accidental B2B leakage | Pricing service boundary + negative tests |
| Duplicate/racing ledger entries | `select_for_update` + conditional unique constraints |
| Stale stock | Celery freshness jobs + UI warnings |
| Permission sprawl | Capability flags on membership + matrix doc |
| Overabstraction | Service layer only where rules exist |

## 16. Key ADRs

See `docs/decisions/`:

- ADR-0001 Django-first modular monolith  
- ADR-0002 Pricing isolation app  
- ADR-0003 OTP auth with provider abstraction  
- ADR-0004 PostgreSQL search first  
