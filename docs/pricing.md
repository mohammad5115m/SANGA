# Pricing — سنگا (SANGA)

## 1. Two channels, and only two

| Code | Audience | Visible where |
|------|----------|---------------|
| `b2c` | Public / retail customers | Public search, storefront, share links, catalogs |
| `b2b` | Colleagues («همکاران») — any active business with an account — and authorised staff | Colleague marketplace, owner inventory |

The two channels are completely independent: setting one says nothing about the
other. A product may have a fixed B2B number and «استعلام قیمت» for the public,
or the reverse.

**There is no third, per-counterparty channel.** `ContactPrice` existed in v1 and
was removed in V2 (`pricing.0003`). It made "what does this cost?" depend on who
was asking in a way sellers could not audit, and it hung off a manually created
Contact — an object the Business directory replaces.

All money is **IRR**. Nothing converts to Toman anywhere; a number stored here is
a number of Rials.

## 2. Storage

`LotPrice(lot, tier, mode, amount, currency, unit, …)` with unique `(lot, tier)`.

| Field | Meaning |
|-------|---------|
| `mode` | `fixed` \| `inquiry` |
| `amount` | `Decimal(14, 2)`, null when `mode=inquiry` |
| `price_confirmed_at` | When the seller last vouched for this number |
| `price_valid_for_days` | How long that vouching lasts |
| `price_expires_at` | Derived on write from the two above; exists so "which prices are stale?" is an indexed query |
| `special_amount` | فروش ویژه price for **this audience** |
| `special_until` | Optional end of the special sale; null means open-ended |

A `CheckConstraint` (`price_fixed_requires_amount`) makes a fixed price without
an amount impossible at the database level.

### Why special-sale pricing lives on the tier row

This is a security decision, not a modelling preference.

A single `special_price` column on the item would be an unlabelled number
sitting outside the tier gate, and the first public template to render it would
leak a B2B figure. On `LotPrice` it inherits exactly the protection `amount`
already has: the audience filter that hides the B2B tier hides its special price
too.

## 3. Resolution

`pricing.services.resolve_visible_prices(item, audience)` is the only way to read
a price.

```python
_AUDIENCE_TIERS = {
    "owner_staff":    ("b2b", "b2c"),
    "b2b_partner":    ("b2b",),
    "b2c_public":     ("b2c",),
    "platform_admin": ("b2b", "b2c"),
}
```

A disallowed tier is **absent from the result**, not blanked. Callers serialize
this dict into templates and JSON, so absence is the only reliable protection.

Every row goes through `price_view()`, which applies expiry and special-sale
rules once so no individual caller can forget them:

| Situation | `amount` | Displayed |
|-----------|----------|-----------|
| `mode=inquiry` | `None` | استعلام قیمت |
| Fixed, fresh | the amount | the number |
| Fixed, live special sale | `special_amount` | the special number, flagged |
| Fixed, **expired** | `None` | استعلام قیمت |

An expired price keeps its stored `amount` so the seller can still see what they
last set. It simply stops being presented as current — a stale number that looks
authoritative is worse than no number.

## 4. Defence in depth

Two independent layers, either of which would be sufficient:

1. **Query layer** — `inventory.policy` prefetches only the tiers the audience
   may see, so a B2B row is never even loaded in memory on a public page.
2. **Resolution layer** — `resolve_visible_prices` filters by audience again.

Templates receive flat, pre-resolved dicts (`b2c_price_context`,
`b2b_price_context`) rather than the tier map, so there is nothing for a
template to walk even by accident. Open Graph metadata uses the same flat
payload.

## 5. Freshness is per channel

Price validity is independent of **stock** validity, and the two B2B/B2C windows
are independent of each other. A seller may trust their stock for ten days, their
colleague price for five, and their retail price for two.

`confirm_lot_price()` restarts a window without changing the number — the common
case after an expiry, when the seller looks at the price and decides it is still
right.
