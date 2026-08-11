# User Flows & Navigation — سنگا (SANGA)

## 1. Information Architecture (Primary Nav)

### A) Business App (authenticated owner/staff)

Mobile bottom nav as built (`templates/layouts/app_shell.html`):

1. **خانه** — Dashboard  
2. **موجودی** — Inventory  
3. **افزودن** — Quick Add (center action)  
4. **بازار** — Partner marketplace  
5. **بیشتر** — Settings hub: contacts, ledger, catalogs, partners, inquiries, purchase requests, demand board, team  

The desktop top bar carries the same destinations plus **رزروها**, **مخاطبین**,
**دفتر حساب**, and **کاتالوگ‌ها** directly. The last three are hidden unless the
membership holds `customers.manage`, `ledger.view`, or `catalog.manage` respectively
— see [permissions.md](./permissions.md) §10. Hiding a link is a courtesy, never the
access control.

### B) B2B Partner surface

There is **no separate partner app**: a partner is just a business, so it uses the
same shell. Its partner-facing destinations are **بازار** (marketplace, with saved
searches and supplier follows), **درخواست‌های خرید** and **تابلوی تقاضا** (both
reached from the settings hub), and **رزروها**.

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
  → Invite employee/partner (skippable)
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
- Row/card indicators: freshness, availability, reservation, visibility, urgent  
- Actions: quick edit, duplicate, archive, hide/show, mark sold, reserve, confirm, media, prices  
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

## 7. B2B Marketplace Flow

Approved partner browses recent/urgent lots → sees B2B price → inquire or request reservation → optional save search / follow supplier.

## 8. Inquiry Flow

```text
New → Viewed → Contacted → Negotiating → Converted / Closed / Lost
```

Linked to a lot or a custom catalog. Assignment to a specific staff member is not
built — `Inquiry` has no assignee field.

## 9. Reservation Flow

```text
Request → Approve / Reject
Approve → Active hold (expires) → Extend / Cancel / Convert
```

Service layer locks lot row; prevents oversell; updates lot status/qty.

## 10. Purchase Request + Matching Flow

Partner publishes structured PR → matching service finds candidate lots → notify relevant sellers → private offers → inquiry/reservation continuation.

No public reverse auction.

## 11. Custom Catalog Sharing Flow

Owner curates lots → generates share link → customer opens B2C-safe view → track views → inquire.

## 12. Page Map (Phase-oriented)

### Phase 1 pages

- Login / OTP  
- Onboarding steps  
- App shell (RTL)  
- Dashboard (lightweight)  
- Business settings / warehouses / team list (basic)  

### Phase 2 pages

- Inventory list/detail  
- Quick add wizard  
- Product picker/create  
- Media manager  
- Price editor  

### Phase 3+

- Storefront + lot public detail  
- Partner marketplace  
- PR board  
- Reservations inbox  
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
