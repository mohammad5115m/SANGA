# User Flows — سنگا (SANGA)

## 1. Navigation

Six primary destinations, matching the six things a seller does:

| Label | What it is |
|-------|-----------|
| خانه | what needs doing today |
| موجودی من | the business's own products |
| بازار | colleagues' products |
| خرید و فروش | purchase requests in both directions, and finalized sales |
| کاتالوگ‌ها | shareable catalogs |
| بیشتر | everything visited weekly rather than hourly |

«بیشتر» holds the colleague directory, ledger, invoices, inquiries, customers,
reports, team and settings. Keeping the primary bar to six items is what makes it
readable on a phone.

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

## 3. Adding a product (4 steps)

```text
1. محصول    — pick an existing product definition or name a new one,
              with stone type, colour, quarry and applications
2. مشخصات   — grade, processing, dimensions, location
3. موجودی و قیمت — stock mode and validity, then both price channels
4. عکس و انتشار — media, then publish or save as a draft
```

Was seven steps. Media and pricing were separate screens each, and a warehouse
had to be chosen before anything else could be entered.

Step 3 asks how long the seller vouches for the numbers, which is what makes the
freshness model work without nagging.

## 4. The product lifecycle

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

## 5. Buying and selling

```text
Buyer finds a product in بازار
  → درخواست خرید (quantity, optional proposed price, note)
  → seller reviews it in درخواست‌های خرید دریافتی
  → seller may change quantity, price and note
  → accept or reject
  → if accepted: «توافق شده — هنوز نهایی نشده»
  → seller performs نهایی کردن فروش
  → Trade created, ledger posted, invoice issued
```

Accepting is **not** selling. See [trading.md](./trading.md).

## 6. Public customer

```text
/search/  →  filter  →  select several products
          →  /inquiry/  →  quantity per product
          →  identity (name + mobile)
          →  OTP verification
          →  saved
          →  optional WhatsApp / Telegram share
```

No login wall, and identity is asked for once, at the end. See
[customers.md](./customers.md).

## 7. Dashboard

Operational, not analytical. There are no charts.

Top of the page is what needs doing: products needing a stock check, prices
needing review, open purchase requests, unanswered inquiries, and a warning when
an agreed sale has not been finalized. Below that: the financial summary and the
largest debtors and creditors (only with `ledger.view`), recent sales and
invoices, and the newest colleague products.

Empty sections are hidden rather than rendered as empty frames; the counters at
the top still say zero.

Every list is bounded and every total is a database aggregate. A test pins the
query count so an N+1 anywhere on the page fails immediately.

## 8. Public URLs

| Path | What |
|------|------|
| `/search/` | cross-seller product discovery |
| `/s/<business_slug>/` | one seller's storefront |
| `/s/<business_slug>/items/<id>/` | product detail |
| `/p/<public_token>/` | stable per-product share link |
| `/c/<share_token>/` | shared catalog |
| `/inquiry/…` | the multi-product inquiry flow |

All unauthenticated, all B2C-safe.
