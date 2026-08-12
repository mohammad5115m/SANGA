# Permissions & Pricing Security — سنگا (SANGA)

## 1. Goals

1. Enforce **tenant isolation** (Business A never reads Business B private data).  
2. Enforce **B2B price non-leakage** to B2C/public audiences.  
3. Make staff permissions **configurable** without hard-coding checks everywhere.  
4. Keep the matrix understandable for non-technical owners.

## 1.1 Account provisioning is admin-only

Before any capability question arises, there is a harder boundary: **only a
Platform Admin creates platform Users and Businesses.**

- Authentication never creates an account. `verify_login_otp` looks the User up
  and refuses when it is missing or inactive.
- Requesting an OTP for an unprovisioned phone still writes a challenge row (so
  the rate limiter cannot be used to enumerate numbers) but sends no SMS.
- Refusal for "no such account" and "account deactivated" uses one shared
  message, so an anonymous caller cannot tell the two apart.
- There is no route that creates a Business. Provisioning happens through
  `./manage.py provision_business`, `./manage.py provision_user`, or Django admin.
- A User with no membership is redirected to `/app/no-business/`, a page with no
  form on it.

Public retail customers are never platform Users. Submitting an inquiry must
never create one.

## 2. Audiences (Resolved at Request Time)

| Audience code | Who | Sees B2B price? | Sees B2C price? | Sees a contact-specific price? |
|---------------|-----|-----------------|-----------------|-------------------------------|
| `owner_staff` | Active membership with price capability | Yes (if `prices.view` / `prices.edit`) | Yes | No (its own screen lists them instead) |
| `b2b_partner` | Any other business with an account («همکار») | Yes (for lots visible to them) | **Never** | Only its own, if the supplier set one |
| `b2c_public` | Anonymous or retail customer | **Never** | Yes (if lot visible in catalog) | **Never** |
| `platform_admin` | Platform operators | Yes (admin tools only) | Yes | No |

The audience code `b2b_partner` is historical: there is no partnership to approve
any more, so it means «every business with an account». End customers never have
accounts and always resolve to `b2c_public`.

**Policy decision (v1):** B2B marketplace shows **B2B price only**. B2C catalog shows **B2C price only**. Owner inventory UI shows both.

A supplier may override the B2B number for one specific contact
(`pricing.ContactPrice`). It applies only when the viewer *is* the business that
contact is linked to, and only through the `b2b_partner` audience — see
[pricing.md](./pricing.md) for the model and the fallback order.

## 3. Capability Codes (Staff)

Stored on `BusinessMembership.permissions` (list of strings), with role defaults.

| Capability | Meaning |
|------------|---------|
| `inventory.view` | View internal inventory |
| `inventory.create` | Create lots/products |
| `inventory.edit` | Edit lot/product fields |
| `inventory.quantity` | Change quantities |
| `inventory.media` | Upload/reorder media |
| `inventory.publish` | Change visibility/status publish actions |
| `inventory.confirm` | One-click freshness confirmation |
| `prices.view` | View B2B+B2C prices, and a contact's contact-specific prices |
| `prices.edit` | Edit prices, including contact-specific overrides (`ContactPrice`) |
| `inquiries.view` | Inquiry inbox; browse own purchase requests and the demand board |
| `inquiries.respond` | Respond to inquiries; create/close purchase requests, submit offers, decide on offers |
| `customers.manage` | CRM and private contacts (contacts app) |
| `catalog.manage` | Custom catalogs / storefront settings |
| `team.manage` | Invite/edit memberships |
| `business.settings` | Business profile/settings |
| `analytics.view` | Reserved for dashboards/reports — **not checked anywhere yet** |
| `audit.view` | Reserved for an audit trail — **not checked anywhere yet**; there is no audit model |
| `ledger.view` | View contact balances & statements |
| `ledger.manage` | Post ledger entries & reversals |

### Role defaults

Defined in `apps/businesses/permissions.py::ROLE_DEFAULTS`:

| Role | Default capabilities |
|------|----------------------|
| `owner` | All (and `has_capability` returns `True` unconditionally) |
| `manager` | All |
| `staff` | `inventory.*`, `prices.view` (not `edit`), `inquiries.view`, `inquiries.respond`, `customers.manage`, `catalog.manage`, `ledger.view` (not `manage`) |
| `viewer` | `inventory.view`, `analytics.view`, `inquiries.view` (read-only) |

> Financial writes (`ledger.manage`) and price edits (`prices.edit`) are limited to
> owner/manager by default; staff can view balances and prices but not post entries
> or change numbers.

**Capability codes are materialized per membership.** `BusinessMembership.save()`
copies the role defaults into the `permissions` JSON list the first time it is
saved, and never refreshes it afterwards. Adding a *new* code to `ALL_CAPABILITIES`
therefore grants it to nobody except owners (who bypass the list entirely) —
existing managers and staff keep the list they were created with. This is why new
features reuse an existing code wherever the meaning fits, and why introducing a
code requires a data migration that appends it to the affected memberships.

The same mechanism means **removing** a code leaves it behind in existing rows.
`partners.manage`, `reservations.view` and `reservations.manage` were deleted from
`ALL_CAPABILITIES` and `ROLE_DEFAULTS` when the partners and reservations apps
were removed, but memberships created earlier still carry those strings in their
`permissions` list. That is harmless — nothing checks them any more, and
`has_capability` only answers questions the code actually asks — so the strings
are deliberately not migrated away.

Owners can customize per membership.

## 4. Tenant Isolation Rules

For every tenant-sensitive selector:

```text
base_qs = Model.objects.filter(business=actor.business)
# then apply object visibility / capability
```

Never trust raw IDs from the client without ownership/access checks.

Mandatory tests:

- User in Business A cannot GET/POST Business B lot by UUID.  
- Another business cannot access A's private lots, contacts, ledger, or inquiries.  
- Public catalog cannot return B2B fields even if guessed.

`apps/marketplace/tests/test_network_privacy.py` holds the last one as a suite:
the network is open, and contacts, ledger balances, the financial summary, the
aging report, private lots and inquiries still stop at the business boundary.

## 5. Visibility Matrix (Inventory Lot)

Three levels, no per-lot allowlist:

| Lot visibility | Label | Owner staff | Any other business with an account | B2C storefront visitor / anonymous |
|----------------|-------|-------------|------------------------------------|------------------------------------|
| `private` | داخلی | Yes | No | No |
| `colleagues` | همکاران | Yes | Yes | No |
| `public` | عمومی | Yes | Yes | Yes |

Prices stay audience-filtered in every cell: a colleague sees the B2B tier (plus
its own `ContactPrice` override, if the supplier set one) and never the B2C tier;
the storefront sees the B2C tier and never a B2B number or an override.

Enforcement lives in `apps.marketplace.selectors.marketplace_lots_for`, which every
marketplace entry point (list, detail by UUID, lot inquiry, saved-search alerts,
the dashboard's «تازه‌ترین محموله‌های همکاران» panel) goes through. The gate is
`visibility IN (colleagues, public)`, minus archived lots, minus the viewer's own
lots — **and both businesses must be `Business.Status.ACTIVE`**: a suspended viewer
gets an empty marketplace and cannot fetch a lot by UUID, and a suspended owner's
lots (with their B2B prices) are listed to nobody. The owner side is a join on the
lot's business, not a per-lot lookup, so the gate costs no extra query. This is the
same notion of "active" that `contacts.is_linkable_business` and
`businesses.get_active_membership` already use.

A lot's `visibility` value is the supplier's own distribution decision and is shown
only on the owner's inventory screens — never on marketplace cards seen by another
business.

## 6. B2B Price Protection Strategy

### Architectural controls

1. **Separate `pricing` app** with `resolve_visible_prices(lot, audience)`.  
2. **No B2B columns** in public catalog query annotations.  
3. Template context processors never inject global price maps.  
4. API serializers: explicit allowlists per audience.  
5. Logging redaction: do not log full price payloads to client-accessible logs.  
6. PWA cache: network-first / no-store for price & stock endpoints.  
7. Share cards / Open Graph: B2C price or “استعلام بگیرید” only.

### Forbidden patterns

- Rendering both prices and hiding B2B with CSS.  
- Embedding B2B in `data-*` attributes for public pages.  
- Returning unused B2B fields “for convenience” in public JSON.

## 7. Colleague Access

The marketplace requires:

1. Authenticated user  
2. Active business membership  
3. An **active** business on both sides (§5)  

That is the whole gate. There is no partnership to request or approve: every
active business with an account sees every other active business's `colleagues`
and `public` lots and their B2B prices. B2B prices are prefetched only for lots that pass the
visibility gate, and a viewer never sees their own lots in the marketplace.

What stays private between businesses regardless: `private` lots, contacts, the
ledger and everything derived from it (balances, statements, financial summary,
aging report), inquiries, and purchase offers. None of these was ever gated by a
partnership — they are scoped by `business` and by `ledger.*` / `customers.manage`
capabilities — so opening the network did not widen them.

Purchase requests are visible to the network when the buyer marks them public;
offers on them stay private between the two parties.

### Contact links

`contacts.Contact.linked_business` may point at any other **active** business, and
at most one contact per business may point at a given business
(`uniq_linked_business_per_business`, re-checked in `contacts.services` with a
Persian error). Without that rule one colleague's balance could silently split
across two ledgers, and a contact-specific price could become ambiguous. Linking
is one-sided bookkeeping: it grants the linked business nothing.

## 8. Demand & Trade Authorization

Purchase requests are the **buyer** side and use `inquiries.*`:

| Action | Capability |
|--------|------------|
| Browse own purchase requests / the demand board | `inquiries.view` |
| Create or cancel a purchase request | `inquiries.respond` |
| Submit or update a private offer | `inquiries.respond` |
| Accept or reject an offer on your own request | `inquiries.respond` |
| Record the trade of an accepted offer in the ledger | `ledger.manage` |

Accepting an offer holds no stock and moves no quantity: it records the decision,
rejects the competing offers, and notifies the seller. Settling the trade is a
separate, deliberate ledger action — see [accounting.md](./accounting.md).

Every one of these is enforced in `purchase_requests.services` (or
`accounting.services`), with the view decorators as a second layer.

### Active business on both sides

The demand board follows the same rule as the marketplace (§5): **both businesses
must be `Business.Status.ACTIVE`**. `purchase_requests.selectors.network_purchase_requests`
returns nothing to a suspended viewer — an empty board and no by-UUID fetch through
`get_network_request` — and filters out requests owned by a suspended business, so a
suspended buyer's demand is shown to nobody. The owner side is a join on the requesting
business, not a per-row lookup. `purchase_requests.services` re-checks both sides where
a business commits to a counterparty: `submit_private_offer` refuses a suspended seller
and a suspended requester, and `decide_offer` refuses when either side has been suspended
since the offer was made.

This gates **participation in the shared network only**. A suspended business keeps full
access to its own data: `my_purchase_requests` / `get_own_request`, its inventory and its
ledger are untouched, and it may still close its own request.

Lots attachable to an offer, a ledger entry, or a custom catalog are restricted to
the acting business's own un-archived lots, in the form *and* again in the service;
a crafted lot UUID is rejected rather than silently ignored.

## 9. Platform Admin

- **Built today:** Django Admin for technical superuser ops  
- **Not built:** a custom `platform_admin` UI for verification, moderation, or
  suspicious-activity workflows (Phase 8)  
- Normal customers never see Django Admin

> `analytics.view` remains a reserved capability and is **not** what powers the
> business dashboard. Financial dashboard panels use `ledger.view` only.

## 10. Navigation

`apps.businesses.context_processors.business_context` exposes `capabilities`, the
frozen set of codes the current membership actually holds (derived from
`has_capability`, so owner bypass and suspended memberships behave identically to
the server-side checks). Templates use it only to hide links that would end in
«دسترسی ندارید» — «مخاطبین» needs `customers.manage`, «دفتر حساب» needs
`ledger.view`, «کاتالوگ‌ها» needs `catalog.manage`. It is **never** a substitute
for the decorator and the service check; it injects no prices and no tenant data.

The dashboard follows the same rule the other way round: its financial sections
(خلاصه مالی and the largest debtors/creditors) are decided in
`apps.businesses.dashboard.dashboard_data`, which only computes them for a
membership holding `ledger.view`. A member without it gets `finance = None` and
empty balance lists **in the response context** — the numbers are never fetched,
let alone rendered and hidden — and still sees the rest of the dashboard.

## 11. Permission Enforcement Checklist (Definition of Done)

For each new endpoint/page:

- [ ] Audience resolved  
- [ ] Tenant scoped  
- [ ] Capability checked  
- [ ] Visibility applied in queryset  
- [ ] Price fields filtered  
- [ ] Negative authz test added when security-sensitive  
