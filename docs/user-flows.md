# User Flows — سنگا (SANGA)

## 1. Navigation

Six primary destinations, matching the six things a seller does:

| Label | What it is |
|-------|-----------|
| خانه | what needs doing today |
| موجودی من | the business's own products |
| بازار | colleagues' products |
| خرید و فروش | bilateral agreements, confirmations, and finalized trades |
| کاتالوگ‌ها | shareable catalogs |
| بیشتر | everything visited weekly rather than hourly |

«بیشتر» holds the colleague directory, ledger, invoices, inquiries, customers,
customer follow-ups, reports, team and settings. Keeping the primary bar to six
items is what makes it readable on a phone.

Gone from the interface entirely: تابلوی تقاضا (the demand board), مخاطبین
(private contacts) and انبارها (warehouses). Navigation follows capabilities, so
a link is never offered that ends in «دسترسی ندارید».

> The word «محموله» does not appear anywhere a user can see. The vocabulary is
> «محصول» and «موجودی محصول». A test asserts this across every main page.

## 2. Account provisioning and first login

There is **no self-service signup**.

```text
Platform Admin provisions Business + Owner User
  (./manage.py provision_business, or Django admin)
  → Admin provisions additional Users (./manage.py provision_user)
  → User logs in with OTP
  → Business profile
  → Dashboard
```

Authentication never creates an account. Requesting an OTP for an unknown phone
produces a normal-looking response but sends no SMS; verifying it fails with a
message that deliberately cannot distinguish "no such account" from "account
deactivated".

A User belonging to no Business lands on `/app/no-business/`, which explains the
situation and links to support. It contains no form.

## 3. Adding or editing a product

One page captures the controlled stone type, optional name suffix, applications,
processing, dimensions, exact-or-blank quantity, both price channels,
descriptions and visibility. The commercial name and immutable product code are
generated automatically. Media is managed from the resulting product page.

There is no existing-product picker, warehouse, stock-mode choice or wizard.
The form still asks how long the seller vouches for numeric stock and prices,
which keeps freshness explicit without nagging.

## 4. Building a catalog

```text
موجودی من → select rows or all current filter results
           → create a catalog or add to an existing catalog
           → enter metadata → share the live link
```

Catalog membership is explicit. Current price, stock, media and eligibility are
resolved when the link is viewed.

## 5. The product lifecycle

Four independent things, never one status field. See
[inventory.md](./inventory.md).

```text
ویرایش · تأیید موجودی · بروزرسانی قیمت
ناموجود کردن / موجود کردن
انتشار / توقف انتشار
اشتراک‌گذاری
حذف محصول
```

All of them are on the product page rather than buried in a menu. Deletion is
styled as destructive and requires confirmation.

## 6. Buying and selling

```text
Seller or buyer records a phone/in-person agreement
  → select the counterparty and own role
  → add registered seller products and/or miscellaneous products
  → enter agreed quantities and prices
  → send to the other Business
  → counterparty confirms or rejects
  → on confirmation: Trade + both ledgers + issued invoice
```

Sending a proposal is **not** selling. See [trading.md](./trading.md).

## 7. Public customer

```text
/store/<storefront_token>/  →  filter  →  select several products
          →  /inquiry/  →  quantity per product
          →  identity (name + mobile)
          →  OTP verification
          →  saved
          →  optional WhatsApp / Telegram share
```

No login wall, and identity is asked for once, at the end. See
[customers.md](./customers.md).

The storefront begins with a plain-language application choice and a simple
search. Technical filters stay behind progressive disclosure. Seller follow-up
then continues at `/app/leads/`: customer profile → note or next follow-up →
overdue/today queue → in-app reminder.

## 8. Dashboard

Operational, not analytical. There are no charts.

Top of the page is what needs doing: products needing a stock check, prices
needing review, trade agreements waiting for this Business's confirmation, and
unanswered inquiries. Below that: the financial summary and the
largest debtors and creditors (only with `ledger.view`), recent sales and
invoices, and the newest colleague products.

Empty sections are hidden rather than rendered as empty frames; the counters at
the top still say zero.

Every list is bounded and every total is a database aggregate. A test pins the
query count so an N+1 anywhere on the page fails immediately.

## 9. Public URLs

| Path | What |
|------|------|
| `/store/<storefront_token>/` | one seller's unlisted storefront |
| `/store/<storefront_token>/items/<id>/` | storefront product detail |
| `/p/<public_token>/` | one product's public share link |
| `/p/<public_token>/` | stable per-product share link |
| `/c/<share_token>/` | shared catalog |
| `/inquiry/…` | the multi-product inquiry flow |

All unauthenticated, all B2C-safe.
