# Roadmap — Sang Esf

## Where things stand

The V2 refactor is complete. What follows is what exists, what was deliberately
removed, and what is genuinely left.

### Shipped

| Area | State |
|------|-------|
| Platform provisioning | Password login and public owner registration requests; admin approval atomically creates the account and Business. OTP login is opt-in. |
| Products | One-page create/edit, controlled stone taxonomy, immutable codes, nullable fresh quantity |
| Pricing | Two independent channels, per-tier validity windows, special sale, expiry degrading to «استعلام قیمت» |
| Discovery | One eligibility policy and one filter schema behind «موجودی من», the marketplace, public search and catalog rules |
| Colleague directory | Every eligible Business, automatically |
| Subscription | `plan`, `seat_limit`, `active_until`, enforced in services |
| Buying and selling | Invoice-first customer, registered-partner and local-partner workflows; old requests remain read-only history |
| Money | Business-counterparty ledger, exactly-once posting, four manual entry types, FIFO aging |
| Invoicing | Immutable signed revisions, locked numbering, exactly-once settlement, cheque history, PDF/PNG and print views |
| Public customers | Account-free browsing and inquiries; name/mobile required on submission, unverified by default, customer OTP opt-in |
| Customer follow-up | Persistent customer profiles, needs, notes, activity history, follow-up queue and in-app reminders |
| Catalogs | Inventory-first explicit selection, with values resolved live against eligibility |
| Reports | Ten operational reports with date ranges and print views |
| UI | Capability-scoped dashboard, received-invoice review queue, one-page product creation and media management; optional MVP navigation |

### Removed, and not coming back

Demand board and free-form purchase requests · seller offers against network
demand · automatic matching · reservations · saved searches and their Celery job
· the hourly freshness sweep · `ContactPrice` · manual Contact CRUD · warehouse
management · three-level visibility · seven dead product statuses · self-service
business creation.

### Explicitly deferred

Online payment · escrow · tax-authority integration ·
logistics · automatic stock synchronisation · reverse auctions · chat · ratings ·
AI recommendations · image similarity search · native apps · advanced BI · PDF
catalogs · complex subscription billing · per-colleague custom prices · advanced
CRM automation and independent deal-pipeline entities.

## Remaining technical debt

Small, known, and none of it blocking.

**`InventoryLot` is still called `InventoryLot`.** The rename to `InventoryItem`
was planned and cut. It is pure internal churn across six apps' foreign keys with
no user-visible benefit, and the part that mattered — removing «محموله» from the
interface — is done. Worth doing during a quiet week, in its own commit, as a
single `RenameModel` plus mechanical `RenameField` operations.

**Five retired apps stay in `INSTALLED_APPS`.** `contacts`, `purchase_requests`,
`partners`, `matching` and `reservations` are migration-history stubs, and
`contacts` still owns a table. Squashing the migration history would let them go,
which is a deliberate, separately-planned operation — removing them now breaks
`migrate` on an empty database.

**Legacy ledger rows are read-only.** Pre-V2 entries whose Contact had no linked
Business keep their balance and are listed at `/app/accounting/legacy/`, but no
new entry can be posted against them. Mapping them to a Business is a support
task, not something to guess at.

**Notifications have no preferences.** Owners and managers get every relevant
notification. Deliberate for MVP; the first complaint is the signal to add
granularity.

**Media has no transcoding or thumbnailing.** Videos are served as uploaded.
Fine at this scale; a large video on a slow connection is the thing that will
prompt revisiting it.

## Natural next steps

Ordered by how likely each is to be asked for:

1. **One-click stock confirmation from the notification.** A buyer can already
   ask, the seller is notified, and confirming restores the number — but the
   seller still has to navigate to the product to do it.
2. **Bulk product actions.** Confirming stock on twenty products one at a time is
   the most obvious remaining friction.
3. **Colleague page financial panel.** Balance and invoices already exist; they
   should appear on the colleague page rather than only in the ledger.
4. **Squash migrations** and retire the five stub apps.
5. **The `InventoryItem` rename**, once the squash makes it cheap.
6. **Notification preferences**, if and when the volume justifies them.
