# Permissions & Pricing Security — سنگا (SANGA)

## 1. Goals

1. Enforce **tenant isolation** (Business A never reads Business B private data).  
2. Enforce **B2B price non-leakage** to B2C/public audiences.  
3. Make staff permissions **configurable** without hard-coding checks everywhere.  
4. Keep the matrix understandable for non-technical owners.

## 2. Audiences (Resolved at Request Time)

| Audience code | Who | Sees B2B price? | Sees B2C price? | Sees a partner-specific price? |
|---------------|-----|-----------------|-----------------|-------------------------------|
| `owner_staff` | Active membership with price capability | Yes (if `prices.view` / `prices.edit`) | Yes | No (its own screen lists them instead) |
| `b2b_partner` | Approved partner relation | Yes (for lots visible to them) | **Never** | Only its own, if the supplier set one |
| `b2c_public` | Anonymous or retail customer | **Never** | Yes (if lot visible in catalog) | **Never** |
| `platform_admin` | Platform operators | Yes (admin tools only) | Yes | No |

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
| `prices.view` | View B2B+B2C prices, and a contact's partner-specific prices |
| `prices.edit` | Edit prices, including partner-specific overrides (`ContactPrice`) |
| `inquiries.view` | Inquiry inbox; browse own purchase requests and the demand board |
| `inquiries.respond` | Respond to inquiries; create/close purchase requests, submit offers, decide on offers |
| `reservations.view` / `reservations.manage` | Reservation workflow (seller-side holds) |
| `partners.manage` | Approve/manage partners |
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
| `staff` | `inventory.*`, `prices.view` (not `edit`), `inquiries.view`, `inquiries.respond`, `reservations.view`, `reservations.manage`, `customers.manage`, `catalog.manage`, `ledger.view` (not `manage`) |
| `viewer` | `inventory.view`, `analytics.view`, `inquiries.view`, `reservations.view` (read-only) |

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
- Partner of A cannot access A's private lots.  
- Public catalog cannot return B2B fields even if guessed.

## 5. Visibility Matrix (Inventory Lot)

| Lot visibility | Owner staff | Approved partner | Business without approved partnership | B2C catalog visitor | Anonymous public discovery |
|----------------|-------------|------------------|---------------------------------------|---------------------|----------------------------|
| `private` | Yes | No | No | No | No |
| `selected_partners` | Yes | Yes | No | No | No |
| `all_partners` | Yes | Yes | No | No | No |
| `customer_catalog` | Yes | No* | No | Yes | No |
| `public` | Yes | Yes** | Yes** | Yes | Yes |

\* Partners do not automatically see customer-catalog-only lots in marketplace unless also published to partners.  
\*\* Public lots may appear in partner search; prices still audience-filtered.

There is **no per-lot partner allowlist**. `selected_partners` is a legacy alias of
`all_partners`: both mean "any business with an approved `PartnerRelation` to the
lot's owner". The stored values are kept distinct only so existing rows remain
readable; the lot editor offers a single "شرکای تأییدشده" option.

Enforcement lives in `apps.marketplace.selectors.marketplace_lots_for`, which every
marketplace entry point (list, detail by UUID, lot inquiry, reservation request,
demand matching, saved-search alerts) goes through.

Persisted `matching.MatchResult` rows are a snapshot of a past match, so they are
**filtered at read time** against the current marketplace visibility through
`apps.matching.selectors.visible_matches_for`, not only pruned on the next rematch.
A revoked partnership, a lot turned `private`, or an archived lot therefore stops
exposing the supplier and product name on the buyer's purchase-request page
immediately; hidden matches are silently omitted. Pruning inside `persist_matches`
is kept as cleanup, not as the security boundary.

A lot's `visibility` value is the supplier's own distribution decision and is shown
only on the owner's inventory screens — never on marketplace or match cards seen by
another business.

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

## 7. Partner Access

Partner marketplace requires:

1. Authenticated user  
2. Active business membership  
3. `PartnerRelation.status == approved` between the viewer and the lot's owning business, for every partner-only visibility (`selected_partners` and `all_partners`)  

A `requested`, `rejected` or `blocked` relation grants nothing. Only `public` lots
are visible to a marketplace member without an approved partnership, and B2B
prices are only prefetched for lots that pass this gate.

Purchase requests/offers remain private between parties.

### Contact links

`contacts.Contact.linked_business` may point only at an approved partner, and at
most one contact per business may point at a given partner
(`uniq_linked_business_per_business`, re-checked in `contacts.services` with a
Persian error). Without that rule one partner's balance could silently split
across two ledgers, and a partner-specific price could become ambiguous.

## 8. Reservation & Demand Authorization

- Reservation requester: partner or authorized customer flow  
- Seller staff: `reservations.manage` to approve/reject/extend  
- Quantity changes go through the reservation service only (locking)

Purchase requests are the **buyer** side and use `inquiries.*`, not
`reservations.*`:

| Action | Capability |
|--------|------------|
| Browse own purchase requests / the demand board | `inquiries.view` |
| Create, re-match, or cancel a purchase request | `inquiries.respond` |
| Submit or update a private offer | `inquiries.respond` |
| Accept or reject an offer on your own request | `inquiries.respond` |

Accepting an offer that names a lot creates the reservation hold as a side effect,
but the buyer is not managing their own stock, so it does **not** require the
seller-side `reservations.manage`. Every one of these is enforced in
`purchase_requests.services`, with the view decorators as a second layer.

Lots attachable to an offer or a custom catalog are restricted to the acting
business's own un-archived lots, in the form *and* again in the service; a crafted
lot UUID is rejected rather than silently ignored.

## 9. Platform Admin

- Django Admin: technical superuser ops  
- `platform_admin` UI: verification, moderation, suspicious activity  
- Normal customers never see Django Admin

## 10. Navigation

`apps.businesses.context_processors.business_context` exposes `capabilities`, the
frozen set of codes the current membership actually holds (derived from
`has_capability`, so owner bypass and suspended memberships behave identically to
the server-side checks). Templates use it only to hide links that would end in
«دسترسی ندارید» — «مخاطبین» needs `customers.manage`, «دفتر حساب» needs
`ledger.view`, «کاتالوگ‌ها» needs `catalog.manage`. It is **never** a substitute
for the decorator and the service check; it injects no prices and no tenant data.

## 11. Permission Enforcement Checklist (Definition of Done)

For each new endpoint/page:

- [ ] Audience resolved  
- [ ] Tenant scoped  
- [ ] Capability checked  
- [ ] Visibility applied in queryset  
- [ ] Price fields filtered  
- [ ] Negative authz test added when security-sensitive  
