# Catalogs — سنگا (SANGA)

## 1. Inventory-first creation

A seller starts in «موجودی من», selects individual items or chooses all items
matching the current filters across every page, then creates a catalog. The
create form asks only for title, customer, message, expiry and active state.

The selection is stored briefly in the seller's session with an opaque token.
It is bound to the current business, expires after one hour and is resolved
again on submit. Filter-based selection therefore means "everything matching
now", while ownership and deletion changes cannot smuggle stale IDs into the
catalog.

Existing catalogs can receive more selected inventory through the same list.

## 2. Explicit membership, live values

`CustomCatalogItem` is a simple ordered membership row. There are no manual,
rule or hybrid modes, no stored filter language and no include/exclude override
states.

A catalog still shows current data: today's price, stock, visibility, media and
descriptions. That is the deliberate opposite of an invoice:

> **Catalog = current. Invoice = historical.**

```text
catalog products = selected membership INTERSECT currently eligible products
```

The eligibility intersection is the security boundary. A hidden, unavailable or
deleted item disappears from a shared catalog without destroying its membership;
if it becomes eligible again, it returns. Another business's item can never be
added by either the UI or the service layer.

## 3. Management and public safety

The management page lists current eligible catalog items, supports removing an
item, adding more from inventory, editing metadata, deactivating the link and
deleting the catalog. Deleting a catalog never deletes inventory.

Public catalog pages use the shared B2C price resolver. B2B price rows are absent
from the public payload. Expired stock and expired prices render as inquiry,
matching storefront and search behaviour.
