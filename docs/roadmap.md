# Implementation Roadmap — سنگا (SANGA)

## Guiding Rule

Implement progressively. Do **not** build the entire product in one uncontrolled pass.  
Each phase must meet Definition of Done: data layer, permissions, UI, RTL, validation, tests, docs.

## Phase 0 — Product & UX Foundation ✅ (this pass)

Deliverables:

- [x] `docs/product.md`
- [x] `docs/architecture.md`
- [x] `docs/data-model.md`
- [x] `docs/permissions.md`
- [x] `docs/user-flows.md`
- [x] `docs/roadmap.md`
- [x] ADRs for key decisions
- [x] Design tokens / component inventory notes

Exit criteria: clear app structure, ER model, permission matrix, navigation IA, milestones.

## Phase 1 — Technical Foundation (in progress)

Completed in foundation pass:

- [x] Django 5 project layout + split settings  
- [x] Docker Compose: web, postgres, redis, worker/beat  
- [x] Custom User + OTP auth (console SMS provider)  
- [x] Business, Membership, Warehouse  
- [x] Role/capability permissions  
- [x] RTL app shell + design-system CSS primitives (Tailwind build pipeline deferred; semantic CSS tokens shipped)  
- [x] Onboarding flow (guided)  
- [x] Lightweight dashboard shell  
- [x] Health checks, `.env.example`, README  
- [x] Inventory/Pricing domain models (UI wizard deferred to Phase 2)  
- [x] Initial automated tests (OTP, tenant, B2B price resolution)

Exit criteria: a user can register via OTP (dev), create a business, add warehouse, see app shell.

## Phase 2 — Inventory Core (heart of the product)

- [x] Product + InventoryLot  
- [x] Pricing tiers B2B/B2C service boundary  
- [x] Media upload on lots (thumbnails deferred refinement)  
- [x] Status + visibility  
- [x] Quick Add wizard (7 steps)  
- [x] Inventory list/detail/edit/duplicate/hide/archive/sold  
- [x] Freshness evaluation + one-click confirm + Celery task  

Exit criteria: register a lot in ~90s; both prices stored; public serializers cannot read B2B.

## Phase 3 — B2C Catalog

- [x] Branded storefront (`/s/{slug}/`)  
- [x] Search/filter  
- [x] Lot detail (consumer copy, B2C price only)  
- [x] Inquiries from catalog + inbox  
- [x] Custom catalogs + share tokens (`/c/{token}/`)  
- [x] Social share meta + compare (B2C-safe)  

## Phase 4 — B2B Partner Network

- [x] Partner approval relations  
- [x] Marketplace browse  
- [x] B2B price visibility (B2B-only payloads)  
- [x] Follow suppliers  
- [x] Saved searches + match notification task  

## Phase 5 — Demand Network

- Purchase requests  
- Private offers  
- Rule-based matching service  

## Phase 6 — Reservations

- Request/approve/reject/extend/cancel/convert  
- Quantity locking  
- Expiration jobs  

## Phase 7 — CRM & Analytics

- Customer profiles  
- Inquiry pipeline UX  
- Business dashboard analytics  
- Platform metrics scaffolding  

## Phase 8 — Trust & Administration

- Verification workflow  
- Moderation  
- Audit UI  
- Trust indicators  

## Phase 9 — Production Hardening

- Security review  
- Performance/N+1 pass  
- Accessibility pass  
- Backup/observability  
- Deployment docs  
- PWA cache policy verification  

## Milestones (Practical)

| Milestone | Outcome |
|-----------|---------|
| M0 | Docs + decisions approved/usable |
| M1 | Runnable foundation + onboarding |
| M2 | Inventory MVP usable daily by a seller |
| M3 | Public catalog shareable to customers |
| M4 | Partners trading via marketplace + reservations |
| M5 | Production-ready hardening |

## Dependencies (Initial Technical)

- Python 3.12+  
- Django 5.x  
- djangorestframework (select APIs)  
- PostgreSQL 16  
- Redis  
- Celery + django-celery-beat  
- django-environ  
- django-storages + boto3 (prod media)  
- Pillow  
- Tailwind CSS (via Node or django-tailwind approach)  
- HTMX + Alpine.js  
- pytest-django  
- factory-boy  
- ruff / black (formatting)  

## Testing Strategy by Phase

- Phase 1: auth, membership, tenant scoping  
- Phase 2: pricing leakage tests, freshness, lot constraints  
- Phase 3: public catalog never includes B2B  
- Phase 4–6: partner authz, reservation concurrency  
- Phase 9: broader e2e critical path  

## Deployment Architecture (Target)

Docker images → Compose/VM initially → Nginx + Gunicorn + Celery + Postgres + Redis + S3-compatible storage.

No Kubernetes in v1.

## What Comes Immediately After This Document Pass

1. Write ADRs + pricing doc  
2. Scaffold project + Docker + settings  
3. Implement accounts/businesses foundation  
4. RTL shell + design system components  
5. Onboarding + verify with tests  

## Future (Do Not Overbuild Now)

Invoices, payments/escrow, logistics, advanced APIs, AI stone assistant, image search, native apps.
