# Permissions & Pricing Security — سنگا (SANGA)

## 1. Goals

1. Enforce **tenant isolation** (Business A never reads Business B private data).  
2. Enforce **B2B price non-leakage** to B2C/public audiences.  
3. Make staff permissions **configurable** without hard-coding checks everywhere.  
4. Keep the matrix understandable for non-technical owners.

## 2. Audiences (Resolved at Request Time)

| Audience code | Who | Sees B2B price? | Sees B2C price? |
|---------------|-----|-----------------|-----------------|
| `owner_staff` | Active membership with price capability | Yes (if `prices.view` / `prices.edit`) | Yes |
| `b2b_partner` | Approved partner relation | Yes (for lots visible to them) | Optional/No by policy (default hide B2C noise) |
| `b2c_public` | Anonymous or retail customer | **Never** | Yes (if lot visible in catalog) |
| `platform_admin` | Platform operators | Yes (admin tools only) | Yes |

**Policy decision (v1):** B2B marketplace shows **B2B price only**. B2C catalog shows **B2C price only**. Owner inventory UI shows both.

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
| `prices.view` | View B2B+B2C prices |
| `prices.edit` | Edit prices |
| `inquiries.view` / `inquiries.respond` | Inquiry pipeline |
| `reservations.view` / `reservations.manage` | Reservation workflow |
| `partners.manage` | Approve/manage partners |
| `customers.manage` | CRM |
| `catalog.manage` | Custom catalogs / storefront settings |
| `team.manage` | Invite/edit memberships |
| `business.settings` | Business profile/settings |
| `analytics.view` | Dashboards/reports |
| `audit.view` | View audit trail |

### Role defaults

| Role | Default capabilities |
|------|----------------------|
| `owner` | All |
| `manager` | All except maybe `team.manage` can be included (include in v1) |
| `staff` | inventory.*, inquiries.*, reservations.view, prices.view (not edit), customers.manage |
| `viewer` | `inventory.view`, `analytics.view` (read-only) |

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

| Lot visibility | Owner staff | Approved partner (all) | Selected partner allowlist | B2C catalog visitor | Anonymous public discovery |
|----------------|-------------|------------------------|----------------------------|---------------------|----------------------------|
| `private` | Yes | No | No | No | No |
| `selected_partners` | Yes | No | Yes | No | No |
| `all_partners` | Yes | Yes | Yes | No | No |
| `customer_catalog` | Yes | No* | No* | Yes | No |
| `public` | Yes | Yes** | Yes** | Yes | Yes |

\* Partners do not automatically see customer-catalog-only lots in marketplace unless also published to partners.  
\*\* Public lots may appear in partner search; prices still audience-filtered.

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
3. `PartnerRelation.status == approved` with supplier (for supplier-specific lots) **or** platform-approved B2B membership flag for “all partners” visibility  

Purchase requests/offers remain private between parties.

## 8. Reservation Authorization

- Requester: partner or authorized customer flow  
- Seller staff: `reservations.manage` to approve/reject/extend  
- Quantity changes go through reservation service only (locking)

## 9. Platform Admin

- Django Admin: technical superuser ops  
- `platform_admin` UI: verification, moderation, suspicious activity  
- Normal customers never see Django Admin

## 10. Permission Enforcement Checklist (Definition of Done)

For each new endpoint/page:

- [ ] Audience resolved  
- [ ] Tenant scoped  
- [ ] Capability checked  
- [ ] Visibility applied in queryset  
- [ ] Price fields filtered  
- [ ] Negative authz test added when security-sensitive  
