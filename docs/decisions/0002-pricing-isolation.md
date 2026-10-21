# ADR-0002: Isolate pricing in a dedicated app

## Status

Accepted; **amended in V2** — the `ContactPrice` part below was superseded

> **V2 amendment.** The pricing app and its audience boundary stand and were
> strengthened. `ContactPrice` — the per-contact override described below — was
> removed: it made "what does this cost?" depend on who was asking in a way
> sellers could not audit, and it hung off a manually created Contact, which the
> Business directory replaces. Freshness and special-sale pricing now live on the
> tier row for the same reason the tier boundary existed in the first place. See
> [pricing.md](../pricing.md).

## Context

B2B prices must never leak to B2C channels. Accidental leakage often happens when prices are columns on the same model freely serialized to all views. Negotiated colleague prices raise the bar further: one contact's override must never appear for another viewer, the public catalog, or anonymous traffic.

## Decision

Create a dedicated `pricing` app with:

- `PriceTier` + `LotPrice` for audience tiers (B2B / B2C)
- `ContactPrice` for one negotiated amount per `(contact, lot)`
- A single resolution path used by all presentation layers:
  `resolve_visible_prices` (audience filter) and
  `resolve_prices_for_viewer` (adds a `"contact"` key only when applicable)

Public/B2C code paths may only request B2C-safe price views. Contact overrides are resolved only for the `b2b_partner` audience when `viewer_business` is the contact's `linked_business`.

## Consequences

- Clear security boundary and test surface (tier leakage + contact override leakage)  
- Slightly more joins/queries than two columns on `InventoryLot`  
- Future tiers do not require rewriting inventory core  
- Contact overrides stay out of inventory/catalog models; tenant rules ride on `contact.business`  
