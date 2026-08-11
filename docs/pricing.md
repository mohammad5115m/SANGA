# Pricing Strategy — سنگا (SANGA)

## 1. Initial Tiers

| Code | Audience | Visible where |
|------|----------|---------------|
| `b2c` | Public/retail | Storefront, custom catalogs, share cards |
| `b2b` | Approved partners + authorized staff | Partner marketplace, owner inventory |

No other tiers in v1. Architecture allows adding tiers later via `PriceTier` + policy mapping.

## 2. Storage

`LotPrice(lot, tier, amount, currency, unit)` with unique `(lot, tier)`.

- `amount`: `Decimal`  
- `currency`: explicit (default `IRR`)  
- `unit`: `per_sqm` | `per_slab` | `inquiry_only`

A lot may be `inquiry_only` for B2C while still having a numeric B2B price.

## 3. Resolution API (Conceptual)

```python
resolve_visible_prices(lot, audience) -> dict[str, PriceView]
```

Examples:

- `b2c_public` → `{ "b2c": ... }` only  
- `b2b_partner` → `{ "b2b": ... }` only  
- `owner_staff` with `prices.view` → `{ "b2b": ..., "b2c": ... }`  

Missing tier → omit or show “استعلام بگیرید” depending on channel.

## 4. Leakage Prevention

See [permissions.md](./permissions.md). Critical rule:

> If a field is not allowed for the audience, it must not exist in the response payload.

## 5. Future Extensibility

Possible later tiers: contractor, export, VIP.  
Add tier row + audience mapping + tests — avoid rewriting inventory models.
