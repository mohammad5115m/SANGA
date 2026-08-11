# ADR-0002: Isolate pricing in a dedicated app

## Status

Accepted

## Context

B2B prices must never leak to B2C channels. Accidental leakage often happens when prices are columns on the same model freely serialized to all views.

## Decision

Create a dedicated `pricing` app with `PriceTier` + `LotPrice` and a single resolution service used by all presentation layers. Public/B2C code paths may only request B2C-safe price views.

## Consequences

- Clear security boundary and test surface  
- Slightly more joins/queries than two columns on `InventoryLot`  
- Future tiers do not require rewriting inventory core  
