# Inventory — سنگا (SANGA)

## 1. One product, one inventory item

`Product` holds the product description and `InventoryLot` holds the sellable
state, but they are one-to-one. Sellers create and edit both through one form;
there is no reusable-product picker and no multi-step wizard.

The product name is derived as `سنگ + stone + optional suffix`. Stone is an
admin-controlled `VocabularyTerm`; sellers cannot create new stone types. The
eight initial terms and code prefixes are:

| Stone | Prefix |
|-------|--------|
| تراورتن | `T` |
| مرمریت | `M` |
| گرانیت | `G` |
| کریستال | `C` |
| مرمر | `O` |
| لایمستون | `L` |
| ترامیت | `TR` |
| چینی | `CH` |

مرمر and مرمریت are distinct. چینی and کریستال are also distinct.

Every inventory item receives a globally unique immutable code containing that
prefix and six safe random characters. Sellers never type or change it.

## 2. Simple specifications

Processing is normalized free text with seller-specific suggestions and defaults
to «ساب خورده». Thickness is entered in centimetres and stored in millimetres.
A blank length means «آزاد».

The old warehouse, location, grade, slab/bundle count, original square metres,
quarry and primary-colour fields are intentionally absent from the product flow.
Applications remain a platform-controlled multi-select.

## 3. Lifecycle and stock truthfulness

Four independent questions remain separate:

| Axis | Field | Meaning |
|------|-------|---------|
| Visibility | `is_visible` | Should buyers see it? |
| Availability | `availability_status` | Is it offered now? |
| Quantity freshness | confirmation + validity fields | Is the number still current? |
| Deletion | `deleted_at` | Is it still an active business object? |

Quantity is nullable. Null means «استعلام موجودی»; a number means the seller has
confirmed an exact square-metre value. There is no stock-mode selector and no
unlimited state.

When a numeric confirmation expires, the stored number is retained for the
seller but buyer surfaces show «استعلام موجودی». The item remains discoverable.
A one-click reconfirm action restarts the window without making the seller type
the same number again.

«ناموجود» is different: it removes the item from all buyer-facing surfaces until
the seller marks it available again. SANGA never decrements stock automatically.

## 4. Eligibility policy

`apps/inventory/policy.py` is the shared buyer-visibility gate:

```text
eligible = not deleted
       AND availability_status = available
       AND is_visible
       AND status = active
       AND seller business is active
```

Marketplace, public search, storefronts, share links and catalogs all use it.
Stock and price freshness deliberately do not remove an item; they change its
display to inquiry.

## 5. Filters and price bounds

`ItemFilterSpec` contains only the product-discovery dimensions retained by the
UI: query, stone, processing, applications, availability, price range and sort.
The owner list adds its lifecycle-state filter.

Price filters are audience-aware: owners and public visitors use B2C; colleagues
use B2B. When a price range is active, inquiry and expired prices do not match.
When it is inactive, those products remain discoverable. Displayed min/max bounds
are computed after all non-price filters, so the UI describes the current result
set rather than the whole database.

## 6. Media, deletion and sharing

`LotMedia` supports ordered images and videos with a primary image. Uploads are
validated by bytes, format, dimensions and size rather than trusting file names
or browser MIME claims.

Deletion is physical when no commercial history exists and soft when invoices,
trades, ledger entries or inquiries depend on the row. Both paths remove catalog
memberships and all buyer visibility.

Every item has a stable opaque `public_token` at `/p/<token>/`. The page always
uses the public/B2C audience, even for a logged-in colleague.
