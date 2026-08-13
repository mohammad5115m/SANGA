# Ledger and Invoices — سنگا (SANGA)

## 1. What this is, and what it is not

The ledger is a **book of record**. It writes down what a colleague owes or is
owed. It does not move money, connect to a bank, or settle anything: payments
happen outside SANGA and are recorded after the fact, exactly as the trader
already does on paper.

Explicitly out of scope, and not "not yet": cheque management, bank
reconciliation, instalment schedules, payment gateways, VAT engines, and
tax-authority integration. A cheque number lives in the «مرجع» field if the user
wants to remember it. Half-implementing any of these produces a second set of
books that disagrees with the real one.

All amounts are **IRR**. Nothing converts to Toman anywhere.

## 2. The counterparty is a Business

V1 keyed the ledger on `contacts.Contact` — a private, hand-typed row per
business. Two people at the same company could become two different debtors, and
nobody could tell without reading both.

V2 keys it on the colleague's **Business** (`LedgerEntry.counterparty_business`).
Every colleague is already a Business in the directory, so there is nothing to
type and nothing to keep in sync.

### Rows that could not be migrated

`accounting.0007` mapped each entry through `Contact.linked_business`. Where
there was no link, the row keeps its `contact` FK and its
`legacy_counterparty_name`, and is:

- still counted in balances and statements,
- listed at `/app/accounting/legacy/` so nobody thinks the money vanished,
- **not** postable to — there is no account to add an entry against,
- **not** reversible, for the same reason.

Inventing a Business for those rows would have put somebody else's money on a
colleague's account. Leaving them queryable under their original name does not.

The backfill re-pointed FKs and copied a name. It did **not** recompute a single
balance: `balance_after` is a stored running total, and rederiving it would be
rewriting financial history to match an assumption.

## 3. Balance convention

From the owning business's books:

| Balance | Label | Meaning |
|---------|-------|---------|
| `> 0` | بدهکار | the colleague owes us (a receivable) |
| `< 0` | بستانکار | we owe the colleague (a payable) |
| `= 0` | تسویه | settled |

`amount` is always a positive magnitude. `balance_delta` carries the sign and is
the single source of truth for balance math. `balance_after` is the running
balance immediately after the entry, computed under a row lock.

**Never render a bare signed number.** `describe_balance()` returns a magnitude
plus a label, because "-500,000" tells the reader nothing about who owes whom.

## 4. Immutability and reversal

Entries are never edited or deleted. `save()` raises on update and `delete()`
raises unconditionally.

Corrections are made by posting a **reversal** that negates the original. The
original is stamped with `reversed_at`, which is a bookkeeping flag rather than
financial data — no amount, delta or balance changes. That stamp is the one
deliberate carve-out from immutability and is written with a queryset
`.update()`, because `save()` blocks updates and must keep doing so.

A reversal cannot itself be reversed, and an entry cannot be reversed twice.

> Because historical models in migrations strip custom methods, a data migration
> *can* rewrite these rows — and is the only thing that can. An equivalent
> management command would fail on the first save.

## 5. One authoritative posting event

A sale reaches the books **exactly once**, when the seller finalizes the trade.

```text
finalize_sale()  /  record_direct_sale()
  → create Trade
  → post_trade_entries()   ← the books move here, and only here
  → create the invoice
  (one transaction)
```

Issuing, re-issuing, printing or cancelling the invoice posts **nothing**. There
is no second way in: `post_manual_entry()` refuses trade entry types outright, so
a sale cannot be typed by hand either, and `create_manual_invoice()` refuses a
colleague counterparty outright, so a document cannot stand in for a sale.

Exactly-once is enforced three ways, so no single mistake breaks it:

1. `select_for_update()` on both parties' Business rows serializes concurrent
   attempts.
2. A pre-check under those locks finds an existing live entry and raises
   `LedgerDuplicateError`.
3. `uniq_trade_entry_per_trade` — a partial unique index on
   `(business, related_trade)` scoped to live trade rows — rejects anything that
   slips past the first two.

`reversed_at__isnull=True` in the constraint means reversing frees the slot, so a
trade recorded with a wrong amount can be corrected and re-recorded with its link
intact.

### Both parties keep books

A colleague sale is one commercial event that moves two ledgers:

| Party | Entry | Effect on their own books |
|-------|-------|---------------------------|
| Seller | `sale` | the colleague becomes **بدهکار** |
| Buyer | `purchase` | the colleague becomes **بستانکار** |

Both are written inside the seller's transaction. The buyer's row is not
bookkeeping the seller is authoring in someone else's name — it is the other half
of a transaction the buyer is a party to, and the buyer can reverse it from their
own statement if it is wrong. Posting only the seller's side left «جمع خرید»
permanently zero for the buyer while the seller's statement said money was owed:
the same event described two incompatible ways.

Idempotency is evaluated **per side**, so a party who reversed their own entry
can have it reposted without disturbing the other's book. Each party owns the
corrections to their own ledger; nothing reaches across the tenant boundary to
reverse somebody else's row.

Both Business rows are locked in ascending stringified-UUID order. Two trades
running in opposite directions between the same pair would otherwise deadlock,
each holding the row the other wants.

A walk-in customer sale posts nothing: there is no colleague account to move, and
inventing one would create a debtor nobody can settle with.

### Who is allowed to post it

Deliberately `sale.finalize`, **not** `ledger.manage`. The entry is a consequence
of the sale the user just completed, not bookkeeping they are authoring.
Requiring `ledger.manage` would mean no salesperson could complete a sale.

Manual entries still require `ledger.manage`. The split lives in
`services._post()`, which does the writing, with the two public entry points
above it owning their own authorization.

## 6. Manual entries: four, and only four

| Type | Label | Effect |
|------|-------|--------|
| `payment_received` | دریافت | colleague owes less |
| `payment_made` | پرداخت | colleague owes more |
| `adjust_debit` | اصلاح بدهکار | colleague owes more |
| `adjust_credit` | اصلاح بستانکار | colleague owes less |

An adjustment must carry a reason. An unexplained correction is
indistinguishable from a mistake when somebody reads the books six months later.

There is no manual "product exchange" entry: goods movements come from finalized
trades.

## 7. Aging (گزارش سنی بدهی)

FIFO. Credits are pooled and applied against the **oldest outstanding debit
first**, so a partial payment clears the oldest invoice rather than being spread
thinly across all of them — that is what makes the «بیش از ۹۰ روز» bucket mean
anything.

A reversed entry and its reversal both drop out of the calculation. Leaving the
reversal in would make it behave like a payment against some *other* debit.

Totals reconcile with the financial summary by construction: `total.total`
equals «جمع مطالبات» and the summed `unapplied_credit` equals «جمع دیون».

Aging is always computed over the whole account. Statement date and type filters
are a viewing device and must not change how old a debt is.

## 7.1 Statement footer: three balances, three questions

| Figure | Question |
|--------|----------|
| مانده ابتدای دوره | where the account stood before the first visible row |
| جمع گردش نمایش‌داده‌شده | what the listed entries moved |
| مانده پایان دوره | the last visible row's running balance |

`balance_after` is a **global** running total: it includes every entry ever
posted, including the ones a filter has hidden. Filter a statement to «دریافت»
only and the closing figure reflects sales that are nowhere on screen — so
closing minus the visible columns did not reconcile, and the footer said nothing
about why. Stating the opening balance is what makes that legible.

With a date filter, opening + debit − credit = closing. With a type filter it
deliberately does not, and now the reader can see it.

## 8. Invoices

Invoices are **historical documents**. `SalesInvoice` and `SalesInvoiceItem`
carry their own copy of the product name, stone type, grade, quantity, unit price
and line total.

Nothing on a rendered invoice reads through to the live product. Rename it,
reprice it, mark it unavailable or delete it — yesterday's invoice is unchanged.
Deleting a product that appears on an invoice archives it rather than purging it,
so the FK survives too.

This is the deliberate opposite of a catalog, which is always live:

> **Catalog = current. Invoice = historical.**

### One Trade, one invoice

`uniq_invoice_per_trade` is a partial unique index on `trade` for non-null rows.
The service was idempotent by lookup, which cannot hold under concurrency: two
requests could both find no invoice for a trade and both create one, documenting
the same sale twice. The service now locks the seller *before* looking, re-checks
under the lock, and turns the constraint violation back into the winning
document, so a double-click still hands both callers the same invoice.

A colleague invoice therefore has exactly one origin: a finalized Trade. Typing
one by hand is refused — see §5.

### Who may read one

The seller sees every document of their own, including drafts. The buyer sees
**issued and cancelled** ones.

A draft is the seller still deciding — the number, the lines, whether to issue it
at all. Showing it to the buyer meant they could read a bill that had not been
sent and watch it change. A cancelled document stays visible because a buyer who
was sent one needs to see that it was voided rather than find it missing.

### Numbering

Sequential per seller, from `Business.invoice_sequence`, incremented under the
`select_for_update()` the allocation already held. The counter only moves
forward, so cancelling an invoice never frees its number for reuse — a gap in the
sequence means something.

It replaced a scan of every invoice the Business had ever issued, performed in
Python to find the maximum, while holding the lock every other salesperson was
queued behind: both the transaction time and the contention grew with the length
of the seller's history. `businesses.0005` seeds each counter from that same
highest-number-so-far, because starting at zero would reissue numbers that
already exist.

`count() + 1` looks obviously correct and produces duplicates the first time two
salespeople issue at the same moment.

### Cancelling

Voiding a document changes no balance. If the sale itself was wrong, the ledger
entry is reversed separately. Keeping the two apart is what stops "I fixed the
invoice" from quietly meaning "I moved money".

### Delivery

A print-friendly view (`/app/invoices/<id>/print/`) shares its body template with
the on-screen view, so the two cannot disagree about what was billed. Browser
printing is the whole delivery mechanism: a PDF pipeline would be a subsystem to
maintain for the same output.
