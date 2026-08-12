# Product Definition — سنگا (SANGA)

> Product name: **SANGA / سنگا**  
> Tagline (product promise): **یک‌بار ثبت موجودی؛ فروش در چند کانال با قیمت و اطلاعات درست برای هر مخاطب**

## 1. Mission

SANGA is a production-grade web platform for natural-stone businesses. It is **not** primarily a generic online shop. It is:

1. **Inventory Management System** — register and keep lots accurate  
2. **Colleague Network («همکاران»)** — every stone business with an account trades
   with every other, at colleague prices  
3. **B2C Digital Catalog** — beautiful customer-facing storefronts  
4. **Demand Board** — purchase requests posted to the network, answered with
   private offers  

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

- payment gateway / escrow / invoicing;
- full logistics / delivery management;
- public reverse auctions;
- star-rating reputation systems;
- AI / image classification / AR;
- native mobile apps (PWA is enough initially);
- microservices / Kubernetes / Elasticsearch-first search.

What *did* get built, and where the line sits: `apps/accounting` is a **ledger of
record** — the business writes down what a contact owes or is owed («دفتر حساب»),
including trades it recorded by hand. It moves no money, issues no invoice, and
settles nothing. Payments are recorded after the fact, as the trader
already does on paper. See [accounting.md](./accounting.md).

## 4. Target Personas

| Persona | Primary job | Key constraint |
|--------|-------------|----------------|
| Business Owner | Run inventory, team, colleagues, analytics | Needs overview without clutter |
| Business Employee | Fast operational work | Permission-scoped tools |
| Colleague («همکار») | Find stock at colleague price | Any stone business with an account |
| B2C Customer | Browse beautiful catalog, inquire | Never has an account; must never see B2B price |
| Platform Admin | Verify businesses, moderate, configure | Custom admin UX + Django Admin for technical ops |

## 5. Core Domain Distinctions

### Product vs Inventory Lot

- **Product**: stable commercial identity of a stone type (name, type, quarry, color, applications, educational copy).  
- **Inventory Lot**: a physical batch available for sale (quantity, dimensions, grade, prices, warehouse, freshness, visibility).

Never collapse these into one model.

### B2B vs B2C Price

- **B2B price**: for colleagues — any business with an account — and owner/staff
  with price permission.
- **B2C price**: for public catalog visitors and retail buyers.
- **Contact-specific price**: one negotiated number for one contact on one lot,
  visible only to the business that contact is linked to. It overrides the B2B tier
  for that colleague and nobody else.

Resolution order for any viewer: contact-specific price → the tier their audience is
allowed to see → «استعلام بگیرید». A missing price is never rendered as zero or blank.

B2B prices must never leak into public HTML, APIs, JS payloads, metadata, logs visible to users, or caches. This is a **security requirement**. A contact-specific price is stricter still: it is dropped for every audience except that one colleague.

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
- Demand posted → private offer conversion  
- Accepted offer → recorded trade conversion  
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

Exactly three levels:

| Visibility | Persian | Audience |
|-----------|---------|----------|
| `private` | داخلی | Owner business only |
| `colleagues` | همکاران | Every business with an account, at B2B prices |
| `public` | عمومی | Colleagues **and** the public storefront, at B2C prices there |

Enforcement must be at query/service level, not only UI.

There is no per-lot allowlist and no partnership: having an *active* account *is*
being a colleague. A `private` lot is invisible to every other business, in the
list and by direct UUID, and a business never sees its own lots in the marketplace.
A suspended business is out of the network in both directions: it browses nothing
and nothing of its own is listed.

## 11. Trust & Verification

Business states: `unverified` → `pending` → `verified` / `rejected` / `suspended`

Prefer objective signals. Built today: **Verified Business**
(`Business.verification_status`) and **Recently Confirmed Inventory**
(`InventoryLot.freshness`). Still intended but not built: average response time,
completed-trade count, inventory-accuracy score.

## 12. Open Product Risks (Tracked)

| Risk | Mitigation |
|------|------------|
| B2B price leakage | Dedicated pricing service + audience serializers + authz tests |
| Stale inventory damages trust | Freshness engine + reminders + auto-hide |
| Overbuilding CRM/accounting | CRM stays a flat contact list of همکاران with no relationship types; the ledger stays a record of debts, with no payments, invoices, or double-entry |
| A suspended business still trading in the network | The marketplace selector requires an active business on both sides, so suspension takes effect in the same query that enforces visibility |
| Archiving a contact quietly erasing their debt | Financial reports keep any archived contact whose balance is not zero, marked «بایگانی‌شده» |
| One colleague's balance splitting across two contacts | A business can be linked to at most one contact per business, enforced by a DB constraint |
| An open network exposing private data | Contacts, ledger, private lots, inquiries and offers are scoped by owning business, independent of who can see the marketplace; covered by `apps/marketplace/tests/test_network_privacy.py` |
| A wrong ledger amount becoming permanent | Entries are immutable; a reversal frees the trade slot so the correct amount can be re-recorded |
| Complex permissions confuse staff | Sensible role defaults + clear Persian labels |
| Public caching of prices/stock | No aggressive PWA cache for inventory/pricing |

## 13. Related Docs

- [architecture.md](./architecture.md)  
- [data-model.md](./data-model.md)  
- [permissions.md](./permissions.md)  
- [user-flows.md](./user-flows.md)  
- [roadmap.md](./roadmap.md)  
- [pricing.md](./pricing.md)  
- [accounting.md](./accounting.md)  
