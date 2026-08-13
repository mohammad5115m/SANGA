# Inventory — سنگا (SANGA)

## 1. Two concepts, kept apart

**Product** — the stable identity of a stone: commercial name, stone type,
quarry/region, colour, applications, general description.

**Sellable item** (`InventoryLot` in code, «محصول قابل فروش» in the UI) — one
sellable instance of that product, with its own grade, processing, dimensions,
quantity, prices, location, images and freshness.

One Product can have several sellable items at once — the same travertine in two
grades and three thicknesses. Collapsing the two would make that impossible to
express, so they stay separate even though the UI keeps the distinction quiet.

> The word «محموله» no longer appears in the interface. The user-facing terms are
> «محصول» and «موجودی محصول».

## 2. Four independent lifecycle axes

The single most important rule in the model. These are never merged into one
status field:

| Axis | Field | Question it answers |
|------|-------|---------------------|
| Visibility | `is_visible` | Should the seller publish this at all? |
| Availability | `availability_status` | Is it offered for sale right now? |
| Stock freshness | `stock_confirmed_at` + `stock_valid_for_days` | Do we trust the quantity? |
| Deletion | `deleted_at` | Should it still exist as an active business object? |

`status` survives with exactly two values, `draft` and `active`, and means only
"has the seller finished creating this yet".

### ناموجود is not استعلام موجودی

The distinction users care about most, and the reason the axes are separate:

| | «ناموجود» | «استعلام موجودی» |
|---|---|---|
| Means | The seller is not offering this right now | The quantity we know is no longer current |
| Field | `availability_status = unavailable` | Derived from stock expiry |
| In search / marketplace / catalogs | **Gone** | **Still there** |
| Can receive requests | No | Yes |
| Reversible | Yes, without recreating the product | Yes, by confirming stock |

## 3. Stock modes

| Mode | UI | Quantity |
|------|----|----------|
| `exact` | a number in m² | required |
| `unlimited` | «موجودی نامحدود» | not asked for |
| `inquiry` | «استعلام موجودی» | never shown |

SANGA is **not** the authoritative warehouse system. It records the last thing
the seller confirmed and is honest about how old that is. Stock is never
decremented automatically by a sale.

When an `exact` or `unlimited` item's confirmation window lapses, the display
degrades to «استعلام موجودی» while the item stays discoverable. The stored figure
is kept so the seller can see what they last said.

Freshness is computed at **read time**. `stock_expires_at` is derived on write so
that "which items need a check?" is an indexed query, but nothing rewrites a row
because time has passed — the hourly Celery sweep that used to mutate `status`
was deleted.

## 4. The eligibility policy

`apps/inventory/policy.py` holds the only definition of a buyer-visible item:

```text
eligible = not deleted
       AND availability_status = available
       AND is_visible
       AND status = active
       AND seller business is active
```

Every buyer-facing surface — colleague marketplace, public search, storefront,
per-product share links, catalogs — goes through `eligible_items()`. Before this
existed the same question was answered by three near-duplicate functions that had
drifted, and the drift was a live leak: the catalog path checked `status` but
forgot `visibility`, so a private item attached to a share link rendered
publicly.

Note what is deliberately **not** in the predicate: stock and price freshness. A
stale item stays discoverable and shows «استعلام موجودی».

Owner-side listing uses `owned_items()` instead, which excludes only deleted
items — a seller has to be able to find an item precisely when it has dropped off
the buyer-facing surfaces.

## 5. One filter schema

`apps/inventory/filters.ItemFilterSpec` is the single serializable filter
vocabulary, shared by «موجودی من», the marketplace, public search and rule-based
catalogs. It round-trips through plain dicts, which is what lets a catalog rule
be *literally* a stored search rather than a second filtering language.

`from_dict` never raises: unknown keys are dropped and unparseable numbers become
`None`, so a hand-edited query string cannot 500 and a rule saved by an older
version of the form keeps resolving.

Price filters and price sorting resolve the tier from the audience, so a public
visitor filtering by price is filtering B2C numbers and a colleague is filtering
B2B ones.

## 6. Applications are a controlled vocabulary

`Application` is a platform-wide taxonomy (`inventory.0007`), not free text.
Application is a primary search facet, and «نمای بیرونی» / «نما بیرونی» /
«نمای خارجی» would otherwise be three facets for one idea. The list is
platform-wide rather than per-business so a colleague's search matches another
seller's items.

## 7. Location replaces warehouses

Warehouse management is gone from the product. Each item carries
`location_province`, `location_city` and `location_address`.

`location_address` is the seller's private information and never appears on a
public page — the public specification table lists fields explicitly rather than
looping over the model, which is how an address ends up leaked.

The `Warehouse` model itself still exists: migration history depends on it, and
`inventory.0005` copies every warehouse address onto its items before the FK
becomes optional.

## 8. Media

`LotMedia` supports any number of images and videos per item, with `sort_order`,
`is_primary` and `caption`. Sellers can reorder, delete and choose the cover
image.

Uploads are validated on extension **and** content type, with size ceilings of
10 MB for images and 60 MB for video. The browser-supplied `Content-Type` is
treated as a hint only: a caller can set any header they like, so trusting it
alone would let an arbitrary file through under an image label.

## 9. Deletion

`delete_item()` inspects live relations before deciding:

- **No commercial history** — the row is really deleted, along with its prices
  and media.
- **Referenced by an invoice, trade, ledger entry or inquiry** — the row is kept
  with `deleted_at` set, so that history stays intact.

Either way the seller sees the same outcome: the product is gone from every list
they manage and every surface a buyer can reach. Manual catalog memberships are
dropped in both branches so an old curated link cannot resurrect it.

The interface never says "soft delete". That is an implementation detail the
seller does not need.

## 10. Share links

Every item has a stable, opaque `public_token` and a share page at
`/p/<token>/`. The token survives edits, so a link pasted into WhatsApp keeps
working.

The share page resolves through the **public** audience even when the visitor is
a logged-in colleague, so pasting a share URL into a colleague's browser cannot
surface a B2B price. Hidden, unavailable and deleted items all return the same
404 page — distinguishing them would tell a stranger which products a seller has
withdrawn.
