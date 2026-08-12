# Accounting Ledger — سنگا (SANGA)

A **practical, internal balance-tracking** feature — not a full accounting, tax,
banking, or payment system. Each private contact has an immutable ledger and a
clearly-labeled balance.

## 1. Balance convention (single, documented)

Balance is always expressed from the **owning business's books**, in standard
Persian bookkeeping vocabulary:

| Balance | State | Label | Meaning |
|---------|-------|-------|---------|
| `> 0` | `debtor` | «بدهکار» | the contact owes the business — a receivable (مطالبات) |
| `< 0` | `creditor` | «بستانکار» | the business owes the contact — a payable (دیون) |
| `= 0` | `settled` | «تسویه» | — |

`describe_balance()` returns `{state, label, amount, signed}` where `amount` is
always the **absolute** magnitude: screens print the label instead of a minus
sign, and a bare signed number is never shown. `signed` is there for callers that
compare or aggregate rather than display.

## 2. Model — `accounting.LedgerEntry`

- `amount` — positive magnitude (`Decimal`, `MinValue 0.01`, check constraint `> 0`).
- `balance_delta` — signed effect on the balance; **single source of truth** for math.
- `balance_after` — running balance immediately after the entry.
- `entry_type` — sale «فروش» / purchase «خرید» / payment_received «دریافت» /
  payment_made «پرداخت» / adjust_debit «اصلاح بدهکار» / adjust_credit
  «اصلاح بستانکار» / reversal «برگشت سند». Values are unchanged; only the display
  labels were professionalised (migration `0004_ledger_entry_type_labels`).
- `occurred_on` (business date), `description`, `reference`, `created_by`, `created_at`.
- Optional links: `related_lot`, `related_offer` (an accepted
  `purchase_requests.PurchaseOffer`), and `reverses` (self-FK).
- `reversed_at` — bookkeeping flag stamped on the *original* entry when a reversal
  is posted. See the carve-out below.

### Direction map

| Type | Δ | Effect | Statement column |
|------|---|--------|------------------|
| sale, payment_made, adjust_debit | `+amount` | contact becomes more بدهکار | بدهکار |
| purchase, payment_received, adjust_credit | `−amount` | contact becomes more بستانکار | بستانکار |
| reversal | `−original.delta` | negates a prior entry | opposite of the original |

`amount` is always positive and every type moves the balance in exactly one
direction, so `balance_delta` is never zero: `is_debit` and `is_credit` are
mutually exclusive and an amount can never appear in both columns.

## 3. Financial correctness

- **Decimal only**, never float; amounts quantized to `0.01`.
- **Concurrency:** posting locks the contact row (`select_for_update`) so entries for
  a contact are strictly serialized; `balance_after = previous + delta` is therefore
  always correct. Posting order = creation order.
- **No independently-editable balance:** the current balance is derived from the
  latest entry's `balance_after`; there is no separate mutable balance field to drift.
  Reconciliation invariant (tested): `sum(balance_delta) == current_balance`.
- **Immutability:** `save()` blocks updates to a posted entry; `delete()` is disabled.
  Corrections are made with **reversal** entries. Double-reversal and reversing a
  reversal are both rejected under the row lock.
- **The one carve-out — `reversed_at`:** `reverse_entry` stamps the original entry
  with the time it was reversed. This is a bookkeeping flag, not financial data:
  no `amount`, `balance_delta`, or `balance_after` is ever changed, and the entry
  still cannot be edited or deleted. Because `save()` deliberately refuses updates
  (and must keep doing so), the stamp is written with a queryset `.update()` inside
  the same transaction and the same contact row lock as the reversal itself, so an
  entry is never observed as reversed without its reversal. It exists so the trade
  idempotency constraint can tell a live trade from a corrected one — see §5.
- **Tenant isolation:** every selector/service is scoped by `business`; cross-business
  access raises a Persian error or 404.
- **Logging:** every post/reversal is logged server-side.

## 4. Capabilities

- `ledger.view` — view balances and statements (staff by default).
- `ledger.manage` — post entries and reversals (owner/manager by default).

## 5. Trades → ledger

An inquiry or an offer is **never** a financial event. Trades in this trade are
agreed offline — by phone, at the quarry, in the workshop — so they are **recorded
manually**, as their own deliberate step.

### Flow

«ثبت معامله» lives at `/app/accounting/record-trade/` and needs `ledger.manage`.

1. Pick the side («فروش» or «خرید»), the contact, the amount, the date, and
   optionally a description, a reference and one of your **own** lots.
2. The screen states the effect on the balance in plain Persian
   («با ثبت این سند، حساب این مخاطب … ریال بدهکار می‌شود»).
   «محاسبه اثر بر مانده» recomputes it server-side after an edit.
3. Only submitting with the confirmation ticked posts the entry — one entry via
   `accounting.services.post_trade_entry`, which reuses the normal `post_entry`
   posting path (same row lock, same balance math, same validation).

The optional related lot must belong to the acting business: the form only offers
its own un-archived lots and the service checks ownership again.

### Starting from an accepted offer

`/app/accounting/record-trade/?offer=<uuid>` pre-fills the screen from an accepted
`PurchaseOffer`: the amount (unit price × offered quantity), the lot, the
counterparty, and the side — «فروش» for the seller who made the offer, «خرید» for
the buyer who accepted it. **Both** sides may record their own entry in their own
ledger; the constraint below is scoped by business precisely so they can.

A business that is party to neither side gets a 404, not a hint that the offer
exists. An offer that was not accepted cannot be recorded at all.

### Contact resolution

Ledger entries attach to a **contact**, never to a counterparty business:

- If exactly one active contact of the acting business has `linked_business` ==
  the offer's counterparty, it is preselected.
- Otherwise the user picks a contact or creates one from the same screen
  (name/phone; linking it to the counterparty is offered because linking no longer
  requires any approval). Contacts are never auto-created, and nothing about the
  counterparty beyond its business name is exposed.

### Idempotency guarantee

An accepted offer yields **at most one live trade entry per business ledger**,
enforced at three levels:

- **Database:** `uniq_trade_entry_per_offer` — a conditional unique constraint on
  `(business, related_offer)` limited to trade types (`sale`, `purchase`) with a
  non-null offer **and `reversed_at IS NULL`**. Reversals, payments and
  adjustments are outside the condition, so corrections stay possible.
- **Service:** `post_trade_entry` re-checks for an existing *un-reversed* trade
  entry after taking the contact row lock, and translates a constraint violation
  (a race on a different contact of the same business) into `LedgerDuplicateError`.
- **View:** a repeat GET/POST finds the existing entry and reports
  «سند مالی این معامله قبلاً ثبت شده است.», then redirects to the statement.

A purely manual trade carries no `related_offer` and is therefore **deliberately
not deduplicated**: nothing outside the ledger identifies an offline trade, so
refusing a second identical one would be guessing. Two genuine sales of the same
amount to the same contact on the same day are both recordable.

### Re-recording a corrected trade

Reversing a trade entry **frees the slot**. Whoever posted the wrong amount
reverses it and records the trade again, with `related_offer` and `related_lot`
still attached, so the deal stays traceable to its offer instead of degrading into
a detached manual entry. Both the constraint and `trade_entry_for_offer` ignore
reversed rows, so the two never disagree.

The ledger keeps all three rows — wrong entry, reversal, corrected entry — and the
reconciliation invariant still holds: `sum(balance_delta) == current_balance`.
A second *un-reversed* trade entry for the same offer is still refused, by the
service pre-check and by the database.

### Amount

Suggested amount = the offer's `unit_price` × `offered_qty_sqm`, quantized to
`0.01` — the number the two sides already agreed on, so no price lookup is
involved. A zero-priced (استعلام-style) offer produces no suggestion. The
suggestion is always editable, because deals are renegotiated offline, and a
manual trade has no suggestion at all.

## 6. Reporting

### 6.1 Statement columns (`contact_statement` + `statement_totals`)

Both the on-screen statement and the printable one show
**تاریخ · شرح · مرجع · بدهکار · بستانکار · مانده**, oldest first. An entry's
`amount` goes in the بدهکار column when `balance_delta > 0` and in the بستانکار
column when it is negative — never both, never a signed number.

`selectors.statement_totals(entries)` takes the already-filtered queryset and
returns:

- `debit` / `credit` — جمع بدهکار and جمع بستانکار, summed in the database over
  exactly the rows on screen, so the footer always matches the active date/type
  filters.
- `closing` / `closing_balance` — «مانده پایان دوره», defined as the running
  balance of the **last row shown** (and its `describe_balance()` labeling). That
  is deliberately the same number as the last مانده cell, so the footer can never
  contradict the table. It is `None` when the filters match nothing, because a
  closing balance for an empty period would be invented.
- `row_count`.

Caveat worth knowing: `balance_after` is a *posting-order* running balance
(entries are ordered by `created_at`). A back-dated entry therefore shows a مانده
that is correct for the ledger as a whole but does not read as a per-date balance.
Aging (below) uses `occurred_on` and is unaffected.

### 6.2 Aging — گزارش سنی بدهی (`accounting/reports.py`)

Outstanding **receivables** split by the age of the debt, in four buckets keyed on
`occurred_on`: جاری (۰ تا ۳۰ روز)، ۳۱ تا ۶۰ روز، ۶۱ تا ۹۰ روز، بیش از ۹۰ روز.
Available per contact (`contact_aging`, shown on the statement screen) and
business-wide (`business_aging`, `/app/accounting/aging/`).

**FIFO allocation rule.** Credit-side entries (دریافت، خرید، اصلاح بستانکار) are
pooled and applied against the **oldest outstanding debit first**, not spread
across all of them. A partial payment therefore clears the oldest invoice and the
«بیش از ۹۰ روز» bucket only holds debt that genuinely stayed unpaid. What survives
of each debit is bucketed by the age of *that debit's* own `occurred_on`.
Allocation order is `occurred_on` then `created_at`, so a back-dated invoice
posted late is still treated as the older debt.

**Reversals** are respected by dropping both sides: an entry stamped `reversed_at`
and the `reversal` entry itself are excluded, so a reversal can never behave like a
payment against some *other* debit. Reversing a payment puts the old debt back in
its bucket.

Consequences that hold by construction (and are tested): a «بستانکار» or «تسویه»
account produces no aging amounts at all, the per-contact aging total equals
`max(0, balance)`, and the leftover `unapplied_credit` equals `max(0, −balance)`.

FIFO cannot be expressed as a plain SQL aggregate, so `business_aging` fetches the
business's live entries in **one** query and groups them per contact in memory,
then reuses the same per-contact routine. It ages **every** contact of the
business, archived ones included, which is what keeps it reconciled with the
summary below — see §6.4.

### 6.3 Business-wide summary (`selectors.business_financial_summary`)

One call, one query (a sub-query over the `contact_balances` annotation — no Python
loop over entries), returning for the business:

| Key | Persian | Meaning |
|-----|---------|---------|
| `receivable_total` | جمع مطالبات | sum of the positive balances |
| `payable_total` | جمع دیون | sum of the negative balances, as a **positive magnitude** |
| `net_balance` / `net` | مانده کل | signed net, plus its labeled `describe_balance()` form |
| `debtor_count`, `creditor_count`, `settled_count`, `contact_count` | — | contact counts per state |

A business with no contacts or no entries yields zeros, never `None`. Reconciles
with the aging report: `receivable_total` equals the aging total and
`payable_total` equals the summed `unapplied_credit`.

### 6.4 Archived contacts — بایگانی ≠ تسویه

Archiving a contact is housekeeping, not a settlement, and it is **not** blocked on
a settled balance: an owner may tidy up a contact they no longer trade with at any
time. Financial reporting therefore refuses to lose sight of the money.

`contact_balances` — and so `business_financial_summary`, which aggregates it —
returns the active contacts **plus every archived contact whose balance is not
zero**. An archived contact whose account is «تسویه» carries no money and is left
out. The rows that survive are marked «بایگانی‌شده» on the ledger index, the aging
report and the dashboard, so a stale row is never mistaken for a live one.

`business_aging` ages every contact of the business for the same reason. The two
still reconcile exactly: an archived «تسویه» account allocates to nothing, so it
contributes zero to both `receivable_total`/`total.total` and
`payable_total`/`unapplied_credit`, whether or not it is included.

The invariant to preserve: **no non-zero balance can be hidden from a financial
report by archiving.**

## 7. UX

- Entry point: `/app/accounting/` opens with the business-wide summary card
  (جمع مطالبات / جمع دیون / مانده کل / counts), then one row per reported contact
  (§6.4) with its balance (summed from `balance_delta` in one query) and its
  بدهکار/بستانکار/تسویه badge. Rows can be filtered by state and sorted by
  balance from the query string (`?state=debtor&sort=debtor`), which is how an
  owner answers «چه کسی به من بدهکار است؟» in one screen. Shown in the app shell
  to members with `ledger.view`.
- The dashboard (`/app/`) repeats خلاصه مالی and the largest debtors/creditors for
  members holding `ledger.view` — the same selectors, no second implementation.
  See [user-flows.md](./user-flows.md) §1.1.
- `/app/accounting/aging/` — the business-wide aging report (§6.2).
- One statement screen per contact: labeled balance card, date/type filters, a
  بدهکار/بستانکار/مانده table with column totals and «مانده پایان دوره», the
  contact's aging breakdown, and a print-to-PDF statement (browser print; no server
  PDF dependency).
- One clear action per screen; manual adjustments require a reason; reversal and
  trade recording each use a confirmation screen.

## 8. Deliberately deferred

- Pairing the two sides of one offer into a single reconciled trade. Each side
  records its own entry independently; nothing checks that the amounts agree.
- Multi-currency (single `IRR` for now).
- Anything beyond the terminology-and-reporting level: no chart of accounts, no
  journal / general-ledger hierarchy, no double-entry account codes, no cheque
  (چک) or instalment tracking, no charts or graphs. The balance stays a single
  per-contact running total.
- Blocking the archiving of a contact whose balance is not تسویه. Deliberately
  **not** built: archiving stays a free housekeeping action and the reports carry
  the debt instead (§6.4).
