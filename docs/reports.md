# Reports — سنگا (SANGA)

## 1. Scope

Tables, totals, filters and a print view. Deliberately not a BI platform: no
charts, no scheduled exports, no data warehouse. A stone trader wants to know who
owes them money and what sold last month, and both are one query.

Every report is a database aggregate over a date-bounded queryset. Nothing loops
over rows in Python to compute a total, so a business with ten thousand trades
costs the same as one with ten.

All reports require `ledger.view` and are scoped to the current Business.

## 2. What is available

| Report | Answers |
|--------|---------|
| خلاصه فروش | total sold, total m², trade count, received, paid, purchased |
| فروش به تفکیک همکار | sales per colleague and per walk-in customer |
| فروش به تفکیک نوع سنگ | sales per stone type |
| فروش به تفکیک محصول | sales per product |
| بدهکاران | who owes us, largest first |
| بستانکاران | whom we owe |
| فاکتورها | invoices in the window, with totals |
| گزارش سنی بدهی | FIFO aging by colleague |
| نیازمند تأیید موجودی | published products whose quantity has gone stale |
| نیازمند بررسی قیمت | fixed prices past their validity window |

The per-colleague account statement lives on the ledger
(`/app/accounting/colleagues/<id>/`), where the invoices exchanged with that
colleague are listed alongside it.

## 3. Two details that are easy to get wrong

**Grouping uses the trade's own snapshot, not the live product.** A product
renamed or reclassified after the sale must not silently move historical revenue
into a different category. `sales_by_stone_type` reads `Trade.stone_type`, which
was copied at finalization.

**Date windows compare dates to dates.** `Trade.finalized_at` is a timestamp, so
filtering it with `__lte=<date>` would drop everything recorded later on the
closing day — an off-by-one that quietly understates the last day of every
report. `DateRange.apply_dt()` exists for exactly that, separately from
`apply()` for real date fields, and there is a test for it.

## 4. What is not date-filtered, and why

Debtors, creditors, aging and the two freshness lists describe **the present**.
"Who owed me money in March" is a different and much harder question than "who
owes me money", and offering a date box that silently answered the second while
looking like the first would be worse than not offering one.

Reversed entries and reversals are excluded from money movement, so a corrected
receipt does not appear as money that arrived.

## 4.1 The invoice total sums issued documents only

Drafts and cancelled invoices are counted, and drafts have their own subtotal, so
neither is missing — but neither is in «مبلغ فاکتورها».

The total used to include everything that was not cancelled, which quietly meant
drafts: documents nobody has been sent, that may still change and may never be
issued. A total that moves while somebody is typing is not a total of anything a
business can act on.

## 5. Printing

`?print=1` renders the same report body in a print sheet. The body template is
shared with the screen view, so the printed numbers cannot differ from the ones
on screen.
