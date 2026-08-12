# Architecture — سنگا (SANGA)

## 1. Repository Assessment (Phase 0)

**Inspection result:** The workspace was empty (no existing Django app, no dependencies, no git history available in PATH).

Therefore we bootstrap a greenfield Django-first monorepo with documentation-first Phase 0, then Phase 1 foundation.

## 2. Architectural Style

**Django modular monolith** with clear domain apps, service/selector layers, and HTMX/Alpine UI.

### Why not microservices / React / Next.js?

- Small product team needs one deployable unit.
- Excellent UX is achievable with Django Templates + HTMX + Alpine + Tailwind.
- React would increase complexity without solving a proven constraint.
- Search starts with PostgreSQL; Meilisearch/ES can plug into a search service later.

## 3. Recommended App Structure

```text
config/                       # project package (settings, urls, asgi/wsgi, celery)
apps/
  core/                       # shared utilities, design-system base templates, middleware, health
  accounts/                   # User, OTP auth, sessions/devices, profiles
  businesses/                 # Business, Membership, Warehouse, verification, onboarding
  inventory/                  # Product, InventoryLot, media, freshness, status
  pricing/                    # PriceTier, LotPrice, audience-aware price resolution
  catalog/                    # public storefront, custom catalogs, sharing previews
  marketplace/                # colleague browse/search experience + saved searches
  purchase_requests/          # PR board + private offers
  inquiries/                  # inquiry pipeline
  contacts/                   # lightweight CRM (private per-business contacts)
  accounting/                 # per-contact ledger + manual trade recording
  notifications/              # in-app/email/SMS abstraction + preferences
  analytics/                  # business + platform metrics
  audit/                      # immutable-ish audit log
  moderation/                 # reports, content moderation, suspicious activity
  platform_admin/             # custom platform admin screens (not Django Admin skin)
design/                       # design tokens, component notes, wireframe notes
docs/                         # product & engineering docs
tests/                        # cross-app integration/e2e helpers when needed
```

### Why this structure (vs dumping everything into `inventory`)?

- **Pricing is isolated** so B2B leakage becomes a hard architectural boundary.
- **Catalog vs marketplace** separate public B2C UX from private B2B UX.
- **Audit / notifications / analytics** remain cross-cutting but not mixed into domain models.

There is no `partners/` app: membership of the network is having an account, so
lot visibility alone decides who sees what, and `marketplace/` owns the B2B
experience (including `SavedSearch`). The `matching/` and `reservations/` apps were
removed with the features they served. All three remain in `INSTALLED_APPS` as
empty, migrations-only packages until the history is squashed.

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
    └── LotPrice (tier=B2B|B2C, amount, currency, unit)
```

Access path:

1. Resolve **audience** (`owner_staff`, `b2b_partner`, `b2c_public`, `platform_admin`).
2. Ask `pricing.services.resolve_prices(lot, audience)`.
3. Serializers/templates receive only allowed price fields.
4. Public catalog endpoints never join/select B2B amounts.

Future tiers = new `PriceTier` rows + policy mapping — not a rewrite.

Details: [permissions.md](./permissions.md), [pricing.md](./pricing.md).

## 8. Search Architecture

```text
SearchService interface
  └── PostgresSearchService (v1)
  └── (future) MeilisearchService
```

Persian normalization (ی/ي، ک/ك، whitespace, ZWNJ) lives in a shared normalizer used by both indexing and query.

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
- Alpine.js for local UI state (tabs, modals, wizard steps)
- Tailwind CSS design system (RTL-first)
- PWA: installable, offline fallback page, **no aggressive caching of prices/stock**

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
| Unit | pricing resolution, freshness, balance math |
| Integration | onboarding, quick-add, demand → offer → recorded trade |
| Authorization | tenant isolation, B2B leakage, visibility matrix |
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
