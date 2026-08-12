# User Flows & Navigation — سنگا (SANGA)

## 1. Information Architecture (Primary Nav)

### A) Business App (authenticated owner/staff)

Mobile bottom nav as built (`templates/layouts/app_shell.html`):

1. **خانه** — Dashboard  
2. **موجودی** — Inventory  
3. **افزودن** — Quick Add (center action)  
4. **بازار** — Colleague marketplace  
5. **بیشتر** — Settings hub: contacts, ledger, catalogs, notifications, inquiries, purchase requests, demand board, team  

### 1.1 Dashboard («خانه», `/app/`)

The first screen after login. Five sections, in order, all tenant-scoped and all
built from existing selectors — the dashboard owns no financial logic of its own:

1. **خلاصه مالی** — جمع مطالبات / جمع دیون / مانده کل, from
   `accounting.selectors.business_financial_summary`. Every number carries its
   بدهکار / بستانکار / تسویه label.
2. **بزرگ‌ترین بدهکاران و بستانکاران** — the five largest balances on each side,
   from `contact_balances`, each row linking to that contact's statement and
   marked «بایگانی‌شده» when the contact is archived
   ([accounting.md](./accounting.md) §6.4).
3. **محموله‌های نیازمند رسیدگی** — one list of the business's own lots that need
   action, with a reason badge per row: «نیاز به تأیید موجودی» and/or
   «بدون قیمت — قابل فروش نیست». One list rather than two, because a lot can have
   both problems and the errand is the same one; the counts sit above it.
4. **تازه‌ترین محموله‌های همکاران** — the newest colleague lots, fetched through
   `marketplace.selectors.marketplace_lots_for` so the visibility rules, the
   "never my own lots" rule and the active-business gate are inherited rather than
   re-implemented. No prices are shown here.
5. **کارهای در انتظار** — unanswered inquiries (still `new`/`viewed`) and offers
   received on this business's own purchase requests that are still `submitted`.

Sections 1 and 2 render **only** for a membership with `ledger.view`; the gate is
in the data layer, not the template ([permissions.md](./permissions.md) §10).
There are no charts — numbers, labels and lists only. Every section has its own
empty state, so a brand-new business sees a coherent screen rather than a frame.
The whole page is a fixed number of queries, pinned by a test.

The desktop top bar carries the same destinations plus **تابلوی تقاضا**,
**مخاطبین**, **دفتر حساب**, and **کاتالوگ‌ها** directly. The last three are hidden
unless the membership holds `customers.manage`, `ledger.view`, or `catalog.manage`
respectively — see [permissions.md](./permissions.md) §10. Hiding a link is a
courtesy, never the access control.

### B) Colleague surface

There is **no separate colleague app**, and nothing to join: a colleague is just a
business with an account, so it uses the same shell. Its network-facing
destinations are **بازار** (marketplace, with saved searches),
**درخواست‌های خرید** and **تابلوی تقاضا**.

### C) B2C Public Catalog

1. Business storefront `/s/{business_slug}/`  
2. Lot detail `/s/{business_slug}/lots/{lot_id}/`  
3. Custom catalog share `/c/{share_token}/`  
4. Inquiry / consultation CTAs  

> URL choice: short `/s/` and `/c/` for shareability; internal app under `/app/`.

## 2. Onboarding Flow (Business)

```text
Create account (OTP)
  → Create business
  → Business profile (city/contact)
  → Add first warehouse
  → Verification info (skippable)
  → Logo/branding (skippable)
  → Add first inventory lot (wizard)
  → Invite teammate (skippable)
  → Dashboard
```

Show progress checklist; allow skip for non-essential steps.

## 3. Quick Add Lot (Target 60–90s)

Mobile-first wizard:

1. Select/create Product  
2. Lot details (code optional/auto, warehouse, grade, processing)  
3. Quantity & dimensions  
4. Photos/video (camera + multi-upload)  
5. Prices (B2B + B2C)  
6. Visibility & publication  
7. Review & publish / save draft  

Supports: autosave draft, duplicate existing lot, primary image selection, compression before upload.

## 4. Inventory Management Flow

- List with filters/chips, search, sort  
- Row/card indicators: freshness, availability, visibility, urgent  
- Actions: quick edit, duplicate, archive, hide/show, mark sold, confirm, media, prices  
- Bulk actions for status/visibility/confirm  

## 5. Freshness Flow

```text
Fresh → Needs Confirmation → Stale → Hidden (configurable)
```

UI shows: `آخرین تأیید موجودی: امروز، ۱۰:۴۵`  
One-click confirm from list/detail/dashboard.  
Celery evaluates + notifies.

## 6. B2C Catalog Flow

Visitor opens storefront → filters/search → lot detail (gallery, B2C price, applications) → inquiry/consultation/share/compare.

Must not expose B2B/internal notes/margins.

## 7. Colleague Marketplace Flow

Any logged-in **active** business browses recent/urgent lots from every other
active business → sees the B2B price (or its own negotiated `ContactPrice`) →
inquires → optionally saves the search. No partnership, no approval, no request. It
never sees its own lots there, nor anybody's `private` lots, nor anything belonging
to a suspended business — and a suspended business sees nothing at all.

## 8. Inquiry Flow

```text
New → Viewed → Contacted → Negotiating → Converted / Closed / Lost
```

Linked to a lot or a custom catalog. Assignment to a specific staff member is not
built — `Inquiry` has no assignee field.

## 9. Trade Recording Flow

```text
Trade agreed offline → «ثبت معامله» → confirm the effect on the balance → one ledger entry
```

Stock is never held by the platform: there are no reservations. Optionally the
screen is opened from an accepted offer, which pre-fills amount, lot, side and
counterparty. See [accounting.md](./accounting.md) §5.

## 10. Purchase Request Flow

Buyer publishes a structured PR → it appears on **تابلوی تقاضا** for every other
business → sellers send **private** offers → the buyer accepts one → either side
records the trade in its own ledger.

There is no automatic matching and no public reverse auction: sellers read the
board themselves, and an offer is visible only to its author and the buyer.

## 11. Custom Catalog Sharing Flow

Owner curates lots → generates share link → customer opens B2C-safe view → track views → inquire.

## 12. Page Map (Phase-oriented)

### Phase 1 pages

- Login / OTP  
- Onboarding steps  
- App shell (RTL)  
- Dashboard (see §1.1)  
- Business settings / warehouses / team list (basic)  

### Phase 2 pages

- Inventory list/detail  
- Quick add wizard  
- Product picker/create  
- Media manager  
- Price editor  

### Phase 3+

- Storefront + lot public detail  
- Colleague marketplace  
- PR board / demand board  
- Ledger + trade recording  
- CRM list/detail  
- Platform verification  

## 13. UI Component System (Design System Targets)

Reusable primitives before feature sprawl:

- Typography scale, spacing, radii, shadows, semantic colors  
- Button, badge, input, select, textarea, checkbox  
- Card (interaction containers only), table + mobile list  
- Modal/dialog, confirm dialog, toast, tabs, breadcrumbs  
- Filter chips, search field, status dots  
- Skeleton, empty state  
- Image gallery, stepper (wizard)  

Persian-first, WCAG-friendly contrast, visible focus, labels on all inputs.

## 14. UX Review Checklist (Every Screen)

- Would a stone seller understand this immediately?  
- Comfortable on a phone?  
- Could B2B info leak?  
- Is inventory trustworthiness visible?  
- Is the primary action obvious?  
- Any unnecessary friction?  
