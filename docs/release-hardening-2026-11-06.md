# Release hardening — 2026-11-06

Branch: `codex/webapp-release-2026-10-24`, continuing from `3ca4e9d`.
Delivery and final-commit CI evidence: [draft PR #10](https://github.com/mohammad5115m/SANGA/pull/10).

## Changes

- Invoice mutations reject suspended/expired actors. Sending and confirming a
  registered-partner invoice recheck both businesses' network eligibility and
  the seller's invoice entitlement using locked, current database rows.
- Invoice-first locking and sorted business locks precede snapshots, numbering
  and financial effects. Browse buyers can configure signatures and approve;
  narrowly authorized senders and offline approvers need no unrelated capability.
- Active actors can still unwind pending documents and settle historical cheques
  after a counterparty's suspension. Historical reads remain available.
- Dashboard queries and markup enforce each section's permissions. The received
  invoice queue replaces unused legacy proposal tasks and links to its filter.
- Signature and invoice controls reflect operational restrictions. Current
  authentication and release documentation now describes the password default,
  reviewed registration, optional customer OTP and all seven CI jobs.

## Verification and limits

Regression coverage includes stale business objects, eligibility changes while
waiting for PostgreSQL locks, custom/revoked permissions, tenant switching,
unchanged signatures/numbering/inventory on refusal, exactly-once financial
posting and bounded dashboard queries. Layout checks use fictional snapshots
at 390, 768 and 1440 pixels; no live business data is modified.

Local checks use the existing Windows virtualenv (Python 3.14). PostgreSQL,
cross-process Redis, native PDF rendering and production-container checks must
pass in Linux CI on the final commit. Windows lacks the GObject/Pango renderer;
its PDF failures do not constitute a successful rendering check.

No schema migration or production configuration change is introduced. Before
deployment, validate real PostgreSQL/Redis credentials, private media and signed
URLs, domains/TLS, administrator access, tested backup restoration and staging
workflows. Follow `deployment.md` and `production-plan-fa.md`. This change does
not deploy or merge the release.
