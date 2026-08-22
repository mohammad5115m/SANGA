# Bilateral colleague trades

SANGA records deals that were already negotiated by phone or in person. It no
longer asks a buyer to start the commercial conversation with an in-app purchase
request.

## Product rule

Either the seller or the buyer may record an agreement. The other Business must
confirm the exact same seller, buyer, lines, quantities and prices before the
agreement becomes a financial event.

```text
draft -> pending -> confirmed
                 -> rejected
      -> cancelled
pending -> cancelled (initiator only)
```

`draft` and `pending` are financially inert. They create no `Trade`, ledger
entry, invoice or invoice number. Confirmation is one atomic database operation:

1. lock and re-read the pending `TradeProposal`;
2. create one immutable `Trade` and its line snapshots;
3. post one sale entry in the seller's book and one purchase entry in the
   buyer's book;
4. allocate and issue one seller invoice;
5. link the proposal to the Trade and mark it confirmed.

If any step fails, all steps roll back. Repeating confirmation returns the same
Trade and cannot duplicate either book or the invoice.

## Lines

A proposal may contain several lines. Each line is one of:

- a product already registered in the seller's inventory; or
- a miscellaneous product named only on this agreement.

Quantity and unit price are agreement-specific. The catalog price is not copied
as an authority and the seller's inventory is not automatically decremented.
Every line freezes product name, stone type, quantity and price so later catalog
edits cannot rewrite history.

## Authorization

| Action | Membership capability | Business requirement |
|---|---|---|
| Create/edit/send own agreement | `trade.propose` | Both parties operational; seller can finalize sales |
| Confirm or reject | `trade.confirm` | Actor is the non-initiating party |
| View final Trade | `trade.propose` | Actor belongs to seller or buyer |
| Issue document during confirmation | consequence of `trade.confirm` | Seller plan includes invoicing |

The author cannot confirm their own proposal. A third Business cannot read or
act on it. Staff defaults include both trade capabilities; custom roles may
separate recording from confirmation.

## Walk-in customers

The manual invoice screen remains separate and only accepts a customer without a
SANGA Business account. It never creates a colleague ledger account. A colleague
invoice must come from bilateral confirmation so the document and both books
cannot disagree.

## Historical purchase requests

`PurchaseRequest` rows and their old URLs remain readable for audit/history.
Their create, respond, cancel and finalize UI actions are retired. Old product
links redirect to a pre-filled bilateral agreement. Legacy service functions
remain temporarily for old records and compatibility tests, but no current UI
creates a new request-era sale.

## Concurrency invariants

- Proposal submissions are idempotent on `(initiated_by_business,
  submission_id)`.
- Confirmation holds the proposal row lock.
- `TradeProposal.trade` is one-to-one.
- Ledger entries are unique per Trade and side.
- Invoices are unique per Trade and invoice numbers are allocated under the
  seller row lock.
- PostgreSQL concurrency tests race two confirmations and assert one Trade, two
  ledger entries and one invoice.
