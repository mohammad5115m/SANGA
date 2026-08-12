# Public Customers — سنگا (SANGA)

## 1. Browsing costs nothing

A retail customer can search, filter, open products, follow share links and
select things to ask about **without logging in, registering, or giving a name
or phone number**.

Identity is asked for exactly once, at submission. Nothing interrupts browsing to
capture a lead, because a lead-capture wall is how a discovery surface stops
being used.

```text
/search/  →  select products  →  /inquiry/  →  identify  →  verify  →  saved
```

## 2. One inquiry, several products

V1 modelled one inquiry per product, so a customer shopping for a floor, a facade
and a staircase had to submit three times and the seller got three unrelated
leads.

V2 has `Inquiry` (the request) and `InquiryItem` (one product plus the metres
needed). The selection lives in the session while browsing — persisting it would
mean identifying the visitor, which is the thing we are avoiding.

The selection is **re-resolved through `eligible_items()` on every read**, so a
product withdrawn while the customer was browsing simply drops out. A seller
never receives a request for something they have taken down.

### Selections spanning several sellers

Public search covers every seller, so a selection can too. At submission the
selection is split by seller and **one inquiry is created per seller**, each
containing only that seller's products. One seller must never see what a customer
asked another.

## 3. Customers are not platform Users

`CustomerLead` is a light identity keyed by `(business, phone)`. It is not an
account: no password, no session, no membership, no way to log in.

Deliberately thin — no pipeline, owner, score or activity feed. It answers one
question, «این مشتری قبلاً چه چیزی پرسیده؟», and stops. Anything more is a CRM,
which is a different product.

Scoped per business, so one seller's customer list is not another's, and two
sellers can hold different names for the same number without conflict.

## 4. Customer OTP

Verification at submission uses `OTPChallenge` with its own
`Purpose.CUSTOMER`, and a separate pair of service functions
(`request_customer_otp` / `verify_customer_otp`) that share only the hashing and
rate-limiting primitives with staff login.

The separation is the point:

- a customer OTP **never creates a User**,
- it **never calls `login()`**,
- a code issued for one purpose **cannot be replayed** against the other.

Success records `phone_verified_at` on the `CustomerLead`. That flag means "this
phone was reachable at that moment" — nothing more.

The SMS provider abstraction is shared, so plugging in a production gateway
lights up both flows at once. Development uses the console provider, which logs
the code instead of sending it.

## 5. Saved first, shared second

The order is a rule, not an implementation detail:

```text
save the inquiry on the server
  → confirm success to the customer
  → then offer WhatsApp / Telegram / copy-link
```

Share buttons are a convenience. A seller must never depend on a message the
customer may not have sent, and the inquiry inbox is the source of truth.

## 6. Seller inbox

`/app/leads/inquiries/` lists every inquiry with the customer, phone, product
count, date and status. `/app/leads/` lists customers, searchable by name or
phone, each linking to their previous requests.

Statuses are four, matching what a seller actually does:

| Status | Meaning |
|--------|---------|
| جدید | nobody has looked at it |
| تماس گرفته‌شده | somebody is on it |
| تبدیل به فروش | it became a sale |
| بسته | finished, either way |

V1 had seven. The middle three (`viewed`, `negotiating`, `lost`) were never
distinguishable in practice, and `inquiries.0004` maps them onto the four above.

Each line keeps a `product_name` snapshot, so a request still reads correctly
after the product is renamed or withdrawn — which is common, since an inquiry is
often *why* the product changes.

## 7. Stock inquiries

When an item's stock confirmation lapses, its display degrades to «استعلام
موجودی» and a buyer can ask whether the quantity still holds. That is recorded
as a normal inquiry so it lands in the same inbox, and the seller answers by
either confirming stock or marking the product ناموجود.
