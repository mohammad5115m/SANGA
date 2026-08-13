# Catalogs — سنگا (SANGA)

## 1. Catalogs are live

A catalog always shows **current** data: today's price, today's stock, today's
photos. Change a product and every catalog containing it changes with it.

This is the deliberate opposite of an invoice, and the contrast is worth
stating plainly because the same products flow through both:

> **Catalog = current. Invoice = historical.**

Nothing is copied into a catalog. There is no snapshot, no cache and nothing to
invalidate, because the resolution happens at read time in the database.

For MVP a catalog is a **web link**. There is no downloadable PDF.

## 2. Three modes

| Mode | What it contains |
|------|------------------|
| `manual` | exactly the products the seller picked |
| `rule` | everything matching a stored filter |
| `hybrid` | the rule, plus manual additions, minus manual removals |

### Rules are stored searches

`CustomCatalog.rules` holds a serialized
`apps.inventory.filters.ItemFilterSpec` — the *same* schema the search bar
produces. A rule catalog is therefore literally a saved search, not a second
filtering language that has to be kept in step with the first.

Incoming rule JSON is round-tripped through `ItemFilterSpec` on save, so unknown
keys are dropped rather than persisted to fail later, and a rule saved by an
older version of the form keeps resolving.

A rule catalog must have at least one filter. An empty rule would silently mean
"everything", which is never what somebody meant to build.

### Manual overrides

`CustomCatalogItem.inclusion` is either `include` or `exclude`. Both exist
because "add this one extra thing" and "not that one" are normal requests, and a
rule that has to encode its own exceptions stops being readable —
«همه تراورتن‌های عباس‌آباد، غیر از این یکی» is two ideas.

A product cannot be both. Setting one replaces the other, because the two
instructions contradict each other and the newer one is what the seller just
said.

## 3. Resolution

```text
catalog products
  = ( products matching the rules
      + manual includes
      - manual excludes )
    INTERSECT currently eligible products
```

The intersection is the security half. `eligible_items(audience="public")` is
the same gate the storefront and search use, so a catalog can never widen
visibility. Concretely:

- A product marked **ناموجود** disappears from every catalog immediately.
- It **returns on its own** when marked available again, if it still matches.
- A **hidden** or **deleted** product never appears, even if it was manually
  included earlier.
- Another business's product can never match, whatever the rule says.

This mattered: before the shared policy existed, attaching a private product to
a catalog published it, complete with its price, to anyone holding the link.

## 4. Public payloads are B2C-safe

Catalog pages render through the same `b2c_price_context` as the storefront, so
they carry a flat, pre-resolved price dict with no tier map to walk. A B2B price
is never loaded into memory on a catalog page, let alone rendered.

Expired stock reads «استعلام موجودی» and an expired price «استعلام قیمت», exactly
as everywhere else.

## 5. Managing a catalog

The manage page shows the **resolved** list — what the customer will actually
see — not the stored rows. For a rule catalog the stored rows are usually empty,
so showing them would show nothing.

From there a seller can exclude a product from a rule catalog without touching
the rule, put it back, deactivate the catalog (the link stops working), or delete
it. Deleting removes the catalog and its link; it does not touch the products,
which is worth saying on the confirmation page because sellers assume otherwise.
