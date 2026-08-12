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

| Audience code | Who | Sees B2B price? | Sees B2C price? |
|---------------|-----|-----------------|-----------------|
| `owner_staff` | Active membership with a price capability | Yes (if `prices.view` / `prices.edit`) | Yes |
| `b2b_partner` | Any other active business with an account («همکار») | Yes | **Never** |
| `b2c_public` | Anonymous or retail customer | **Never** | Yes |
| `platform_admin` | Platform operators | Yes (admin tools only) | Yes |

The code `b2b_partner` is historical: there is no partnership to approve, so it
means «every active business with an account». Public customers never have
accounts and always resolve to `b2c_public`.

**There is no third, per-counterparty price.** `ContactPrice` was removed in V2;
see [pricing.md](./pricing.md).

A **share link** (`/p/<token>/`) always resolves as `b2c_public`, even when the
visitor is a signed-in colleague. Pasting a share URL into a colleague's browser
must not surface a B2B number.

## 3. Two independent gates

Every protected action has to pass **both**:

```text
plan says the Business may do this      (apps/businesses/entitlements.py)
AND
membership says this User may do this   (apps/businesses/permissions.py)
```

They answer different questions and are stored in different places. A seller
whose subscription lapsed still has `sale.finalize` on their membership; they
simply cannot use it.

### 3.1 Plan entitlements (what the Business bought)

| Plan | Can |
|------|-----|
| `browse` | Log in, search the marketplace, view colleagues, send purchase requests, receive invoices, see its own records |
| `seller` | All of that, plus create/publish products, receive purchase requests, finalize sales, manage catalogs, issue invoices, use the ledger |

`Business.seat_limit` caps how many *active* memberships may share the account.
It is checked when a membership is created or reactivated, not at login: lowering
a limit must not lock out people already working, it bites the next time someone
is added.

`Business.active_until` is optional. **Null means no expiry, not expired** — a
field an admin forgot to fill in must not lock the account out overnight.

Enforcement lives in services via `require_entitlement()`, never in templates. A
browse-only account stopped only by hidden navigation is not stopped at all: the
form still posts.

### 3.2 Capability codes (what the member may do)

Stored on `BusinessMembership.permissions` (list of strings), with role defaults.

| Capability | Meaning |
|------------|---------|
| `inventory.view` | See the business's own products |
| `inventory.create` | Create products |
| `inventory.edit` | Edit product fields |
| `inventory.quantity` | Change quantities and stock mode |
| `inventory.media` | Upload, reorder and delete media |
| `inventory.publish` | Publish / unpublish |
| `inventory.confirm` | Confirm stock |
| `prices.view` | See B2B and B2C prices |
| `prices.edit` | Change prices |
| `purchase.request` | Send purchase requests to colleagues |
| `sale.finalize` | Turn an accepted request into a finalized sale |
| `invoice.view` | See invoices |
| `invoice.manage` | Issue and manage invoices |
| `ledger.view` | See balances and statements |
| `ledger.manage` | Post ledger entries and reversals |
| `leads.view` | See customer inquiries |
| `leads.manage` | Respond to customer inquiries |
| `catalog.manage` | Create and manage catalogs |
| `team.manage` | Manage memberships |
| `business.settings` | Business profile and settings |

### Role defaults

| Role | Default capabilities |
|------|----------------------|
| `owner` | All (and `has_capability` returns `True` unconditionally) |
| `manager` | All |
| `staff` | Products, `prices.view`, buying and selling, `invoice.view`, leads, catalogs, `ledger.view` |
| `viewer` | `inventory.view`, `leads.view` |

Financial writes (`ledger.manage`, `invoice.manage`) and price edits
(`prices.edit`) are owner/manager by default: staff can see balances and prices
but not change them.

### Capability codes are materialized, and that is a hazard

`BusinessMembership.save()` copies the role defaults into the `permissions` JSON
list the first time it is saved, and **never refreshes it**.

Two consequences that have bitten this codebase:

- Adding a new code grants it to nobody except owners (who bypass the list).
- Renaming a code silently revokes access for every existing member.

So every capability change ships with a paired data migration. V2's rename is
`businesses.0003`, which maps `inquiries.view` → `leads.view`,
`inquiries.respond` → `leads.manage` (plus the implied `purchase.request` and
`sale.finalize`), `customers.manage` → `leads.manage`, and drops `analytics.view`
and `audit.view` — both declared in v1 and never checked by anything.

Stale strings left over from removed apps (`partners.manage`,
`reservations.*`) are deliberately not migrated away: nothing reads them, and
`has_capability` only answers questions the code actually asks.

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

## 5. Buyer-facing eligibility

Visibility is now a single boolean, and it is one of four independent lifecycle
axes — see [inventory.md](./inventory.md).

`apps/inventory/policy.py::eligible_items()` is the **only** definition of a
buyer-visible product:

```text
not deleted AND available AND is_visible AND status=active AND seller business active
```

Every buyer-facing surface goes through it: the colleague marketplace, public
search, a seller's storefront, per-product share links, and catalogs.

This is not tidiness. Before it existed, three near-duplicate functions answered
the same question and had drifted: the shared-catalog path checked `status` but
forgot `visibility`, so a private product attached to a catalog rendered
publicly, with its B2C price, to anyone holding the link. Consolidating the rule
is what makes that class of bug impossible to reintroduce in one surface at a
time.

Prices stay audience-filtered on top of eligibility: the prefetch loads only the
permitted tier, and `resolve_visible_prices` filters again.

A seller's own management screens use `owned_items()` instead, which excludes
only deleted products — they must be able to find a product precisely when it has
dropped off the buyer-facing surfaces.

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

## 7. Colleague access

The marketplace and the colleague directory require:

1. an authenticated user,
2. an active business membership,
3. an **active** business on both sides.

That is the whole gate. There is no partnership to request or approve: every
active business sees every other active business's published products and their
B2B prices, and never its own in the marketplace.

What stays private between businesses regardless: unpublished products, the
ledger and everything derived from it (balances, statements, summaries, aging),
customer inquiries and leads, and purchase requests the business is not a party
to. None of these was ever gated by a partnership — they are scoped by
`business` and by capability — so opening the network did not widen them.

### The colleague *is* the Business

There is no `Contact` to create, link or maintain. A colleague is a Business, so
two people at the same company cannot become two different debtors, and there is
nothing to keep in sync.

`contacts.Contact` survives only as a target for pre-V2 ledger rows whose
counterparty could not be mapped. It has no UI. See
[accounting.md](./accounting.md) §2.

## 8. Buying and selling authorization

| Action | Capability | Plan entitlement |
|--------|-----------|------------------|
| Send a purchase request | `purchase.request` | none — browse-only accounts can buy |
| Answer a request (accept / reject / adjust) | `purchase.request` | `receive_purchase_requests` |
| Finalize a sale | `sale.finalize` | `finalize_sales` |
| Post the sale's ledger entry | `sale.finalize` | `finalize_sales` |
| Post a manual ledger entry | `ledger.manage` | `manage_ledger` |
| Issue an invoice | `invoice.manage` | `issue_invoices` |

The sale's ledger entry is authorized by `sale.finalize`, **not** `ledger.manage`.
It is a consequence of the sale the user just completed, not bookkeeping they are
authoring — requiring `ledger.manage` would mean no salesperson could complete a
sale. Manual entries still require it.

Accepting a request holds no stock and moves no money. Finalizing is a separate,
deliberate action. See [trading.md](./trading.md).

### Both sides must be active

Same rule as the marketplace: a suspended business neither sends nor receives.
`eligible_items()` refuses a suspended viewer and hides a suspended seller, and
the plan is re-read from the locked row at finalization time, so a subscription
that lapsed while the page was open still blocks the sale.

A suspended business keeps full access to its own data — its products, its own
records and its ledger are untouched. The gate is on **participation in the
shared network**.

Products attachable to a request, a ledger entry or a catalog are restricted to
the acting business's own non-deleted products, in the form *and* again in the
service. A crafted UUID is rejected rather than silently ignored.

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
the server-side checks). It also exposes `entitlements`, the plan's set.

Templates use both **only** to hide links that would end in «دسترسی ندارید» —
«دفتر حساب» needs `ledger.view`, «کاتالوگ‌ها» needs `catalog.manage`,
«خرید و فروش» needs `purchase.request`. Neither is a substitute for the decorator
and the service check, and neither injects prices or tenant data.

A browse-only account stopped by a missing menu item is not stopped at all: the
form still posts. That is why every plan gate is also in the service.

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
- [ ] Capability checked in the **service**, not only the view
- [ ] Plan entitlement checked where the action is a seller action
- [ ] Buyer-facing reads go through `eligible_items()`
- [ ] Price fields filtered by audience
- [ ] Negative authorization test added when security-sensitive
