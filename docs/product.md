# Product Definition — سنگا (SANGA)

> **SANGA / سنگا**
> Product promise: **محصول را یک‌بار ثبت کنید؛ همکاران و مشتریان آن را با اطلاعات
> و قیمت درست برای خودشان ببینند — و همیشه معلوم باشد این اطلاعات چقدر تازه است.**

## 1. What SANGA is

A product discovery, colleague trading, catalog, invoicing and account-ledger
platform for natural-stone businesses.

It helps sellers keep availability and prices reasonably current **without
pretending to be the authoritative warehouse or payment system**. That caveat is
the product, not a limitation of it: the alternative is a system that confidently
reports numbers nobody has checked.

### Core pillars

1. Product discovery
2. B2B colleague marketplace
3. Public B2C discovery
4. Product-bound purchase requests
5. Finalized trades and invoices
6. Per-colleague account ledger
7. Dynamic catalogs
8. Freshness-aware price and stock information

## 2. The problem

Stone sellers keep inventory in Excel, WhatsApp and memory. They quote different
prices to wholesalers and retail buyers by hand, sometimes send the wrong one,
and lose trust when a quoted stone turns out to be gone.

SANGA makes the product the source of truth, with audience-aware pricing and
visibility enforced in the backend, and with every quantity and price carrying an
explicit "we last checked this on…".

## 3. What SANGA is not

Not "not yet" — not at all:

| Not | Because |
|-----|---------|
| A payment gateway or escrow | Money moves outside SANGA and is recorded after the fact |
| A cheque register or bank reconciliation | Half of it produces books that disagree with the real ones |
| A warehouse management system | SANGA cannot know about the sale made over the phone an hour ago |
| An ERP or official accounting system | No VAT engine, no tax-authority integration, no fiscal device |
| A logistics or shipment tracker | Different product |
| A reverse auction or public demand board | Tried in v1; produced no sale either side could point at |
| A CRM | `CustomerLead` answers one question and stops |
| An analytics platform | Reports are tables and totals, not dashboards |

Also deferred: PDF catalogs, per-colleague custom prices, chat, ratings, AI
recommendations, image similarity search, native apps.

## 4. Principles

**The four lifecycle axes are separate.** Visibility, availability, stock
freshness and deletion answer four different questions and never share a field.
«ناموجود» removes a product from every buyer surface; «استعلام موجودی» does not.
See [inventory.md](./inventory.md).

**One eligibility policy.** Every buyer-facing surface asks
`inventory.policy.eligible_items()`. Three near-copies of that question drifted
apart in v1 and the drift was a live data leak.

**One filter schema.** «موجودی من», the marketplace and public search share
`ItemFilterSpec`. Catalog creation can select all current matches without
persisting a second filtering language.

**Current versus historical is a modelling decision.** A catalog always renders
live data. An invoice never changes. Same products, two representations, on
purpose.

**Exactly-once where money is involved.** Finalizing a sale is the single
authoritative financial event, enforced by a row lock, a pre-check and a database
constraint.

**Accounts are provisioned, not signed up for.** Only a Platform Admin creates a
Business or a User. Public customers are never platform Users.

## 5. Personas

| Persona | Job | Constraint |
|---------|-----|-----------|
| Business Owner | Run products, team, colleagues, money | Overview without clutter |
| Salesperson | Add products, answer requests, close sales | Cannot post ledger entries or issue invoices by default |
| Colleague («همکار») | Find stock at colleague prices | Any active business with an account |
| Browse-only Business | Search and request, cannot sell | Blocked in services, not just navigation |
| Public customer | Browse and inquire | Never has an account; never sees a B2B price |
| Platform Admin | Provision businesses and users | Django admin plus two management commands |

Provisioning is also the **approval**. There is no self-service signup, so a
Business exists because an admin checked who it was, and `create_business_for_owner`
records that as `verification_status=VERIFIED`. Only verified businesses appear in
the colleague directory, the marketplace, public search or shared catalogs. A
business that loses that status keeps its own records and its accounting history
— it stops being shown to other people, which is a different thing from being
shut out.

## 6. Success metrics

Product metrics, not necessarily instrumented yet:

- active visible products
- share of products with current stock information
- share with current price information
- B2B product views → purchase requests
- accepted requests → finalized sales
- public product views → customer inquiries
- inquiries containing more than one product
- sales containing more than one product line
- invoices issued
- returning marketplace users
- time to create or update a product
- products confirmed after a stock inquiry

The v1 metrics were about demand-board posts and offers. Those measured activity
on a feature nobody completed a sale through.
