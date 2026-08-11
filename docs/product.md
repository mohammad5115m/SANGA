# Product Definition — سنگا (SANGA)

> Product name: **SANGA / سنگا**  
> Tagline (product promise): **یک‌بار ثبت موجودی؛ فروش در چند کانال با قیمت و اطلاعات درست برای هر مخاطب**

## 1. Mission

SANGA is a production-grade web platform for natural-stone businesses. It is **not** primarily a generic online shop. It is:

1. **Inventory Management System** — register and keep lots accurate  
2. **B2B Partner Network** — private supply network with partner pricing  
3. **B2C Digital Catalog** — beautiful customer-facing storefronts  
4. **Demand Matching Platform** — purchase requests matched to inventory  

The central promise:

> Register inventory once and sell it through multiple channels with the correct information and price for each audience.

## 2. Problem Statement

Stone sellers today typically:

- keep inventory in Excel / WhatsApp / memory;
- quote different prices to wholesalers and retail buyers manually;
- accidentally share wholesale numbers with consumers;
- lose trust when stock is outdated;
- struggle to show attractive catalogs to end customers while protecting B2B margins.

SANGA solves this by making **inventory the source of truth**, with audience-aware pricing and visibility enforced in the backend.

## 3. Non-Goals (Initial Product)

Do **not** build in early phases:

- payment gateway / escrow / accounting;
- full logistics / delivery management;
- public reverse auctions;
- star-rating reputation systems;
- AI / image classification / AR;
- native mobile apps (PWA is enough initially);
- microservices / Kubernetes / Elasticsearch-first search.

## 4. Target Personas

| Persona | Primary job | Key constraint |
|--------|-------------|----------------|
| Business Owner | Run inventory, team, partners, analytics | Needs overview without clutter |
| Business Employee | Fast operational work | Permission-scoped tools |
| B2B Partner | Find stock at wholesale price | Approved membership only |
| B2C Customer | Browse beautiful catalog, inquire | Must never see B2B price |
| Platform Admin | Verify businesses, moderate, configure | Custom admin UX + Django Admin for technical ops |

## 5. Core Domain Distinctions

### Product vs Inventory Lot

- **Product**: stable commercial identity of a stone type (name, type, quarry, color, applications, educational copy).  
- **Inventory Lot**: a physical batch available for sale (quantity, dimensions, grade, prices, warehouse, freshness, visibility).

Never collapse these into one model.

### B2B vs B2C Price

- **B2B price**: only for authorized/approved partners (and owner/staff with price permission).  
- **B2C price**: for public catalog visitors and retail buyers.

B2B prices must never leak into public HTML, APIs, JS payloads, metadata, logs visible to users, or caches. This is a **security requirement**.

## 6. Product Pillars (Priority Order)

When trade-offs conflict, prefer this order:

1. Excellent user experience  
2. Data privacy and pricing security  
3. Correct / fresh inventory information  
4. Simplicity for non-technical users  
5. Business workflow correctness  
6. Maintainable architecture  
7. Performance  
8. Extensibility  
9. Advanced features  

## 7. Success Metrics

### Primary product metric

**Successful verified supply–demand matches per week**

### Supporting metrics

- Fresh inventory percentage  
- Active lots  
- Search → inquiry conversion  
- Inquiry → reservation conversion  
- Reservation → transaction conversion  
- Catalog views and catalog → inquiry conversion  
- Weekly active sellers / returning B2B users  
- Average inventory creation time (target: 60–90 seconds for skilled users)

## 8. Language & Market

- Primary UI language: **Persian (fa)** with full **RTL**  
- Architecture must support future English via Django i18n  
- Demo data uses realistic Iranian stone names but is clearly fictional  

## 9. UX Principles

- Mobile-first, touch-friendly, image-focused  
- Not Django Admin; not a generic developer dashboard  
- Most frequent ops = 1–2 obvious actions  
- Inventory registration wizard, not a giant form  
- Trust signals based on operational facts (verified, recently confirmed), not vanity ratings  

## 10. Visibility Channels (Owner-Controlled)

Each lot can appear in:

| Visibility | Audience |
|-----------|----------|
| Private / internal | Owner business only |
| Selected partners | Explicit partner allowlist |
| All approved B2B partners | Partner marketplace |
| Customer catalog | Business public storefront |
| Public | Broader public discovery (later / optional) |

Enforcement must be at query/service level, not only UI.

## 11. Trust & Verification

Business states: `unverified` → `pending` → `verified` / `rejected` / `suspended`

Prefer objective signals:

- Verified Business  
- Recently Confirmed Inventory  
- Average Response Time  
- Successful Reservations  
- Inventory Accuracy  

## 12. Open Product Risks (Tracked)

| Risk | Mitigation |
|------|------------|
| B2B price leakage | Dedicated pricing service + audience serializers + authz tests |
| Stale inventory damages trust | Freshness engine + reminders + auto-hide |
| Overbuilding CRM/accounting | Keep CRM lightweight; no finance module in v1 |
| Complex permissions confuse staff | Sensible role defaults + clear Persian labels |
| Public caching of prices/stock | No aggressive PWA cache for inventory/pricing |

## 13. Related Docs

- [architecture.md](./architecture.md)  
- [data-model.md](./data-model.md)  
- [permissions.md](./permissions.md)  
- [user-flows.md](./user-flows.md)  
- [roadmap.md](./roadmap.md)  
- [pricing.md](./pricing.md) (created in Phase 1 foundation)  
