# Accounting Ledger — سنگا (SANGA)

A **practical, internal balance-tracking** feature — not a full accounting, tax,
banking, or payment system. Each private contact has an immutable ledger and a
clearly-labeled balance.

## 1. Balance convention (single, documented)

Balance is always expressed from the **owning business's perspective**:

| Balance | Meaning (Persian label) |
|---------|-------------------------|
| `> 0` | «طلب ما از این مخاطب» — the contact owes us |
| `< 0` | «بدهی ما به این مخاطب» — we owe the contact |
| `= 0` | «تسویه» |

A bare signed number is never shown without this label (`describe_balance()`).

## 2. Model — `accounting.LedgerEntry`

- `amount` — positive magnitude (`Decimal`, `MinValue 0.01`, check constraint `> 0`).
- `balance_delta` — signed effect on the balance; **single source of truth** for math.
- `balance_after` — running balance immediately after the entry.
- `entry_type` — sale / purchase / payment_received / payment_made / adjust_debit /
  adjust_credit / reversal.
- `occurred_on` (business date), `description`, `reference`, `created_by`, `created_at`.
- Optional links: `related_lot`, `related_reservation`, and `reverses` (self-FK).
- `reversed_at` — bookkeeping flag stamped on the *original* entry when a reversal
  is posted. See the carve-out below.

### Direction map

| Type | Δ | Effect |
|------|---|--------|
| sale, payment_made, adjust_debit | `+amount` | contact owes us more |
| purchase, payment_received, adjust_credit | `−amount` | contact owes us less |
| reversal | `−original.delta` | negates a prior entry |

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

## 5. Trades → ledger (seller side)

A reservation, inquiry or offer is **never** a financial event. Converting a
reservation to a sale (`reservations.services.convert_reservation`) still writes
nothing to any ledger. Recording the money is a separate, explicit step.

### Flow

1. Seller converts the reservation as before (non-financial).
2. The reservation detail screen then offers «ثبت سند مالی» (only for the seller,
   only while no un-reversed entry exists, only with `ledger.manage`).
3. That screen shows the lot, quantity, suggested amount, chosen contact and a
   plain-Persian statement of the effect on the balance
   («با ثبت این سند، طلب ما از این مخاطب … ریال می‌شود»). «محاسبه اثر بر مانده»
   recomputes it server-side after an edit.
4. Only submitting that screen with the confirmation ticked posts the entry —
   a single `SALE` entry via `accounting.services.post_trade_entry`, which reuses
   the normal `post_entry` posting path (same row lock, same balance math).

Programmatic callers that want conversion and posting in one transaction pass a
`TradeEntryRequest` to `convert_reservation(trade_entry=…)`; a `LedgerError` then
rolls the conversion back too. Omitting it keeps conversion non-financial.

### Contact resolution

Ledger entries attach to a **contact**, never to a buyer business:

- If exactly one active contact of the seller has `linked_business` == the
  reservation's requester business, it is preselected.
- Otherwise the seller picks a contact or creates one from the confirmation
  screen (name/phone; linking to the buyer is offered only when the two
  businesses are approved partners). Contacts are never auto-created, and nothing
  about the buyer beyond its business name is exposed.

### Idempotency guarantee

A reservation yields **at most one live trade entry per business ledger**, enforced
at three levels:

- **Database:** `uniq_trade_entry_per_reservation` — a conditional unique
  constraint on `(business, related_reservation)` limited to trade types
  (`sale`, `purchase`) with a non-null reservation **and `reversed_at IS NULL`**.
  Reversals, payments and adjustments are outside the condition, so corrections
  stay possible. The constraint is scoped by business so the postponed buyer-side
  mirror still fits.
- **Service:** `post_trade_entry` re-checks for an existing *un-reversed* trade
  entry after taking the contact row lock, and translates a constraint violation
  (a race on a different contact of the same business) into `LedgerDuplicateError`.
- **View:** a repeat GET/POST finds the existing entry and reports
  «سند مالی این معامله قبلاً ثبت شده است.», then redirects to the statement.

### Re-recording a corrected trade

Reversing a trade entry **frees the slot**. A seller who posted the wrong amount
reverses it and records the trade again, with `related_reservation` and
`related_lot` still attached, so the deal stays traceable to its reservation
instead of degrading into a detached manual entry. Both the constraint and
`trade_entry_for_reservation` ignore reversed rows, so the two never disagree.

The ledger keeps all three rows — wrong entry, reversal, corrected entry — and the
reconciliation invariant still holds: `sum(balance_delta) == current_balance`.
A second *un-reversed* trade entry for the same reservation is still refused, by
the service pre-check and by the database.

### Amount

Suggested amount = the lot's active B2B **per-square-metre** price ×
`quantity_sqm`, quantized to `0.01`. Inquiry-only, per-slab or missing prices
produce no suggestion and the seller must type the amount. The suggestion is
always editable, because deals are renegotiated offline.

## 6. UX

- Entry point: `/app/accounting/` lists every active contact with its balance
  (summed from `balance_delta` in one query) and links to each statement. Shown in
  the app shell to members with `ledger.view`.
- One statement screen per contact: labeled balance card, date/type filters, entries
  oldest→newest with a running balance, and a print-to-PDF statement (browser print;
  no server PDF dependency).
- One clear action per screen; manual adjustments require a reason; reversal and
  trade recording each use a confirmation screen.

## 7. Deliberately deferred

- Buyer-side `PURCHASE` mirror of a trade (the buyer recording the same deal in
  their own ledger against their own contact). The constraint and services are
  shaped for it, but no code posts it.
- Trades from sources other than reservation conversion (manual trade, accepted
  offer without a reservation).
- Using an accepted offer's `unit_price` as the suggested amount instead of the
  lot's B2B price.
- Multi-currency (single `IRR` for now).
