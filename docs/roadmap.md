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

## Phase 1 — Technical Foundation ✅

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

## Phase 4 — Colleague Network («همکاران»)

- [x] Marketplace browse  
- [x] B2B price visibility (B2B-only payloads)  
- [x] Saved searches + match notification task (now in `apps.marketplace`)  
- [x] **Open network** — an account *is* the membership: no approval, no
  partnership request, no following. Every business sees every other business's
  `colleagues` and `public` lots at B2B prices, minus its own. Visibility collapsed
  to three levels (`private` / `colleagues` / `public`).  
- ~~Partner approval relations~~ — removed with `PartnerRelation`.  
- ~~Follow suppliers~~ — removed with `SupplierFollow`.  

## Phase 5 — Demand Network

- [x] Purchase requests on a board every business can read  
- [x] Private offers (not public reverse auction)  
- ~~Rule-based matching service + persisted `MatchResult`~~ — removed: sellers read
  the board themselves rather than being scored into it.  

## Phase 6 — ~~Reservations~~ (removed)

The platform no longer holds stock. Requesting/approving/extending a hold,
quantity locking and expiration jobs are gone with `apps.reservations`; an accepted
offer is simply accepted, and the money side is recorded by hand — see Phase 7.  

## Phase 7 — Contacts, Accounting & Analytics

Reframed toward the traditional stone trade: private contacts + a practical
per-contact balance ledger, delivered in small slices.

- [x] **Contacts (CRM-lite)** — private per-business contacts, notes, optional link
  to any other *active* business, free-text search, detail, CRUD with archive
  confirmation, an «نمایش مخاطبین بایگانی‌شده» list filter and a POST-only restore.
  Gated by the `customers.manage` capability; tenant isolation + link
  privacy enforced in selectors/services. Relationship types
  (customer/supplier/trader) were **removed** in `contacts.0004_remove_contact_types`:
  only stone sellers and traders hold accounts, so every contact is a همکار.
- [x] **Accounting ledger** — immutable `LedgerEntry` per contact, running balance
  (`balance_after` computed under a per-contact row lock), signed `balance_delta`
  as the single source of truth, reversal entries for corrections (no edits/deletes),
  manual debit/credit with a required reason, date/type filters, and a
  print-to-PDF statement. Gated by `ledger.view` / `ledger.manage`; tenant isolation
  enforced in selectors/services. See [accounting.md](./accounting.md).
- [x] **Connect trades → ledger** — trades are recorded manually («ثبت معامله»):
  side, contact, amount, date, optional own lot, with the balance effect shown in
  plain Persian before a confirmed submit posts one entry. Optionally started from
  an accepted `PurchaseOffer`, which pre-fills amount, lot, side and counterparty;
  either side may record its own entry. At most one *live* trade entry per
  (business, offer), enforced by a conditional unique constraint plus a re-check
  under the contact row lock, so retries and double submits are reported, not
  duplicated. A purely manual trade is deliberately not deduplicated. Gated by
  `ledger.manage` in the service layer. See [accounting.md](./accounting.md).
- [x] **Re-recording a corrected trade** — reversing a trade entry stamps
  `LedgerEntry.reversed_at` and frees the idempotency slot, so a wrong amount can be
  reversed and re-recorded with the offer link intact. A second *un-reversed*
  trade entry is still refused. See [accounting.md](./accounting.md).
- [x] **Contact-specific price override** — `pricing.ContactPrice`, one
  amount per (contact, lot). Fallback: contact-specific → B2B/B2C tier →
  «استعلام بگیرید». Applies only in the colleague marketplace and only to the business
  the contact is linked to; the public catalog and anonymous viewers never see one.
  Managed from the lot's contact-price screen under `prices.edit`, shown read-only on
  the contact page. See [pricing.md](./pricing.md).
- [x] **Business dashboard** — the five sections in
  [user-flows.md](./user-flows.md) §1.1: خلاصه مالی, largest debtors/creditors,
  lots needing attention (unconfirmed or unpriced), newest colleague lots through
  the marketplace selector, and pending work (unanswered inquiries and offers).
  Financial sections are computed only for `ledger.view`; the whole page is a fixed
  number of queries with a test bounding it. No charts.
- [x] **Archived contacts stay on the books** — financial reporting keeps any
  archived contact with a non-zero balance, marked «بایگانی‌شده»; archiving itself
  is still unrestricted. See [accounting.md](./accounting.md) §6.4.
- [x] **Suspended businesses leave the network** — the marketplace selector
  requires an active business on both sides. See [permissions.md](./permissions.md) §5.
- [ ] Inquiry pipeline UX  
- [ ] Analytics beyond the dashboard (trends, conversion, charts)  
- [ ] Platform metrics scaffolding  

Also closed in this slice (authorization/navigation debt, not new product):
purchase requests and offers are gated by `inquiries.view` / `inquiries.respond`
instead of being open to any logged-in member; attaching a lot to a catalog
or an offer rejects a crafted lot id instead of dropping it; and the ledger has a
real entry point at `/app/accounting/`.

### Balance convention (documented, used everywhere)

Balance is stored/computed from the **owning business's perspective**:

- `balance > 0` ⇒ «طرف‌حساب به ما بدهکار است» (they owe us)
- `balance < 0` ⇒ «ما به طرف‌حساب بدهکاریم» (we owe them)
- `balance == 0` ⇒ تسویه

A bare signed number is never shown without this label. Money is always `Decimal`.

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
| M4 | Colleagues trading via marketplace + demand board |
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
- Hand-written CSS tokens (`static/css/app.css`); Tailwind deferred  
- HTMX (+ Alpine.js available)  
- pytest-django  
- ruff / black (formatting)  

## Testing Strategy by Phase

- Phase 1: auth, membership, tenant scoping  
- Phase 2: pricing leakage tests, freshness, lot constraints  
- Phase 3: public catalog never includes B2B  
- Phase 4–5 + Phase 7 trade path: colleague-network privacy (what an open
  marketplace must *not* expose), trade-recording idempotency under concurrency,
  contacts / ledger / ContactPrice / dashboard gates  
- Phase 9: broader e2e critical path  

## Deployment Architecture (Target)

Docker images → Compose/VM initially → Nginx + Gunicorn + Celery + Postgres + Redis + S3-compatible storage.

No Kubernetes in v1.

## Future (Do Not Overbuild Now)

Invoices, payments/escrow, logistics, advanced APIs, AI stone assistant, image search, native apps.
