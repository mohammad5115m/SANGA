# Pricing Strategy — سنگا (SANGA)

## 1. Initial Tiers

| Code | Audience | Visible where |
|------|----------|---------------|
| `b2c` | Public/retail | Storefront, custom catalogs, share cards |
| `b2b` | Approved partners + authorized staff | Partner marketplace, owner inventory |

No other tiers in v1. Architecture allows adding tiers later via `PriceTier` + policy mapping.

## 2. Storage

`LotPrice(lot, tier, amount, currency, unit)` with unique `(lot, tier)`.

- `amount`: `Decimal(14, 2)`  
- `currency`: explicit (default `IRR`)  
- `unit`: `per_sqm` | `per_slab` | `inquiry_only`

A lot may be `inquiry_only` for B2C while still having a numeric B2B price.

### Partner-specific override — `pricing.ContactPrice`

`ContactPrice(contact, lot, amount, currency, unit, created_by, timestamps)` with
unique `(contact, lot)`. Same `amount`/`currency`/`unit` semantics as `LotPrice`.

This is **not** a rules engine — it is one plain number for one contact on one lot:

- Tenant scoping rides on `contact.business`. `set_contact_price` refuses unless
  `contact.business_id == lot.business_id`, so a business can never price another
  business's lot or price against another business's contact.
- An override reaches a viewer **only** when the viewer *is* the business that the
  contact's `linked_business` points at, and only through the `b2b_partner`
  audience. A contact with no `linked_business` can hold an override, but nobody
  ever sees it.
- Since `contacts.Contact` enforces one contact per linked partner per business
  (`uniq_linked_business_per_business`), a partner can never match two overrides.
- Archiving the contact (`is_active = False`) withdraws the override; the partner
  falls back to the B2B tier.
- Managing overrides requires `prices.edit`, enforced in the service, not only in
  the view. The screen is `/app/inventory/lots/<lot_id>/partner-prices/`; the
  contact detail page shows a contact's overrides read-only.

## 3. Resolution API

```python
resolve_visible_prices(lot, audience) -> dict[str, PriceView]
resolve_prices_for_viewer(lot, audience, viewer_business=None) -> dict[str, PriceView]
effective_price(prices, audience) -> PriceView | None
```

`resolve_visible_prices` is unchanged and remains the audience filter.
`resolve_prices_for_viewer` calls it first and then *adds* any applicable override
under the `"contact"` key, so an override goes through the same filter rather than
around it. `resolve_contact_price` returns `None` for every audience except
`b2b_partner` and for `viewer_business is None`, so the public catalog and
anonymous visitors are excluded by construction, not by caller discipline.

Examples:

- `b2c_public` → `{ "b2c": ... }` only — never `"b2b"`, never `"contact"`  
- `b2b_partner` → `{ "b2b": ... }`, plus `{ "contact": ... }` for the linked partner  
- `owner_staff` with `prices.view` → `{ "b2b": ..., "b2c": ... }`  

### Fallback order

`effective_price` picks exactly one price to display:

| Audience | Order |
|----------|-------|
| `b2b_partner` | partner-specific override → `b2b` tier → nothing |
| `b2c_public` | `b2c` tier → nothing |
| `owner_staff`, `platform_admin` | `b2b` → `b2c` |

"Nothing" — no applicable price, or an `inquiry_only` unit — renders as
**«استعلام بگیرید»**, never a blank or a zero.

`marketplace.services.b2b_price_context(lot, viewer_business)` is the single B2B
payload builder and returns `is_partner_price` so the UI can say whose price it is.
`marketplace.selectors.marketplace_lots_for` prefetches only the viewer's own
overrides, so list pages cost no extra query per lot and no other partner's
negotiated price is ever loaded into memory.

## 4. Leakage Prevention

See [permissions.md](./permissions.md). Critical rule:

> If a field is not allowed for the audience, it must not exist in the response payload.

## 5. Future Extensibility

Possible later tiers: contractor, export, VIP.  
Add tier row + audience mapping + tests — avoid rewriting inventory models.
A per-contact override is intentionally *not* a tier: it belongs to one contact,
not to an audience, which is why it lives in its own table and its own pseudo-code
(`"contact"`) rather than in `PriceTier`.
