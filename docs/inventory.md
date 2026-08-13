# Inventory
 — سنگا (SANGA)

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
B2B ones. The price annotation is a tier-scoped subquery rather than a join, so a
price filter cannot multiply rows and needs no `.distinct()` to undo the damage.

### Filters answer with the value the viewer is shown

A filter must return what the card says, and the two used to disagree. Cards
degrade a stale quantity to «استعلام موجودی» and an expired fixed price to
«استعلام قیمت», but the filters compared the stored `available_sqm`,
`stock_mode` and `amount` columns directly. «حداقل ۱۰۰ متر» therefore returned
items that, on the same page, said they had no current quantity, and a price
range returned items whose own card refused to quote a price.

`apps/inventory/queries.py` and `apps/pricing/queries.py` hold the query half of
those definitions, beside the property half on the models:

| Question | Answered with |
|----------|---------------|
| minimum quantity | `current_quantity_q` — unlimited or a still-confirmed exact number |
| stock mode | `effective_stock_mode_q` — including everything expired into inquiry |
| price range and sorting | `effective_amount_subquery` — a live special, else a fresh fixed price, else NULL |
| «فقط فروش ویژه» | `live_special_subquery` |

An expired number is not a smaller number; it is no number, and it must not
answer a question about quantity or price. Items with no current price sort last
in both directions, because «ارزان‌ترین» must not be led by things that have no
price at all.

## 5a. Searchable text is normalized on the way in

`normalize_persian_text` existed and was applied to the incoming search query. It
was never applied to what was stored, which protected one side of a comparison
and neither side of the problem: a product entered on an Arabic keyboard —
«مرمريت» with ي — was invisible to a search typed on a Persian one, and the
reverse. Both keyboards are ordinary.

Normalization now happens on **write**, in `apps/inventory/services.py`, so the
stored value is the one that will be searched. Doing it on read would mean every
query paying for a scan, and would still count two spellings of one stone as two
things in every report.

Orthography is only half of it. «کریستال» and «چینی» are the same stone and no
letter-level normalization will ever join them, so `VocabularyTerm` holds a
platform-wide controlled list per dimension with the synonyms that mean each
term:

| Dimension | Controlled | Why |
|-----------|-----------|-----|
| Stone type | yes | The main facet buyers filter on |
| Primary colour | yes | Small, closed, and heavily filtered |
| Processing/finish | yes | Same |
| Quarry/region | no | Hundreds of Iranian quarries; a closed list is wrong within a month |
| Grade/sort | no | The industry genuinely does not share one vocabulary |

The controlled dimensions are offered as a `<datalist>`, not a `<select>`. A
value that matches no term is still stored — normalized — because Iranian stone
naming has a long tail, and a seller who cannot record the stone they actually
have stops recording stone.

`Trade`, `TradeItem`, `SalesInvoiceItem` and inquiry line snapshots are
deliberately **not** normalized. Those are historical commercial facts on
documents that may already have been handed to a customer; they are never
searched, so normalizing them buys nothing and rewriting them would be a silent
change to history.

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

Uploads are validated on their **bytes**, not on what they claim to be. The
extension, the browser's `Content-Type` and `mimetypes.guess_type` are all
supplied by the caller and all agree with each other about a file that is not an
image at all, so `payload.html` renamed to `stone.jpg` used to pass every check.

Images go through `apps/inventory/media_validation.py`:

| Check | Limit | Why |
|-------|-------|-----|
| Bytes | 10 MB | Refused before anything is decoded |
| Format | JPEG, PNG, WebP, GIF | A valid TIFF is a valid image and still not a product photo |
| Longest edge | 12,000 px | Well past any camera |
| Total pixels | 60 million | The number that actually bounds memory |
| Decodes fully | `verify()` then `load()` | A header parses over truncated data; the pixels do not |

The pixel limit is the one that is easy to leave out, and the byte limit does not
substitute for it. Compression ratios in the thousands are ordinary for synthetic
images, so a 40 KB PNG declaring 30,000 × 30,000 passes a 10 MB check and then
asks for gigabytes while decoding. The dimensions are therefore read from the
header and refused **before** `load()` — after it, the memory has already been
spent. `Image.MAX_IMAGE_PIXELS` is set alongside the explicit check rather than
relied upon, because Pillow only *warns* between one and two times that value,
and a warning does not stop a decode.

Video is checked against its **container signature only** — MP4/MOV/WebM — and
this is a deliberate limitation rather than an oversight. Full validation means
ffprobe, which means ffmpeg in the image: a large dependency with a large CVE
history, for a product that stores occasional short clips. What stands in for it
is the 60 MB size limit, the closed container list, and `Content-Type` plus
`X-Content-Type-Options: nosniff` on the stored object, so a file that turns out
not to be a video is still never executed in SANGA's origin. Revisit when video
becomes a real part of the product.

None of this is a virus scanner and none of it claims to be. The property being
defended is narrower and worth having on its own: whatever reaches storage and is
served back to a browser is a media file.

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
