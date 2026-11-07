# Platform admin review — 2026-11-08

The live console was inspected through its authenticated browser session using
read-only navigation. No production records or credentials were changed. The
fixes are in the application source and need the new image installed on the VPS.

## Findings and corrections

- Creating a business owner or member previously left a new password-login
  account without a usable password. Both workflows now require and validate a
  matching password for new accounts. Existing credentials remain unchanged;
  account creation races cannot silently reset them. Optional OTP still permits
  an explicitly unusable password. Password fields are excluded from error
  reports and never redisplayed after validation failure.
- Application section links inherited the global dashboard template, producing
  an empty overview instead of a model list. A scoped section template restores
  the permitted links without querying global metrics.
- Customer notes/follow-ups, inventory products and catalog/collection items
  accepted unrelated business selections. Admin forms now reject these
  relationships before saving, including catalog inlines. Existing tenant
  ownership is fixed for customers, products, lots and collections; uniqueness
  validation still runs and reports normal form errors.
- Follow-up edits now derive reminder times, maintain completion timestamps and
  clear consumed reminder markers when the schedule changes. Invalid reminder
  intervals produce form errors.
- Account editing normalizes Persian/Arabic/international mobile input. The
  current platform administrator cannot deactivate their own account, and
  account deletion is disabled to retain ownership and audit links.
- Search accepts localized phone digits and international formatting. The
  pending-business link retains its filter, activity dates use Jalali format,
  and the greeting displays the account name.
- The member form explains when a business must be created first. Tablet forms
  no longer compress their fields beside the sidebar, and responsive headers
  expand to contain the account controls.

## Validation and limits

- 102 observed admin destinations were inspected read-only on the live server.
  The new deployment had very few records, so this is navigation coverage, not
  evidence that all possible populated-data states have been exercised.
- Mutating workflows were verified with synthetic records in isolated test
  databases. The focused admin suite passed **72 tests** locally.
- Seven representative pages were checked at 390, 768 and 1440 pixels (21
  layouts). No document overflow or clipped header controls remained. The
  local browser console reported no errors, and the Jalali picker opened.
  Preview screenshots use synthetic identities, not production contact data.
- Ruff, Django system checks and migration-drift checks passed locally.
  Before installation, require all seven CI jobs on the final PR head: full
  SQLite/PostgreSQL suites, PostgreSQL concurrency and real Redis, fresh
  SQLite/PostgreSQL migrations, production settings/container/PDF checks, and
  dependency audit. CI results and the offline archive must identify the same
  source commit. The native PDF runtime is verified on Linux CI, not Windows.

There are no schema changes, new migrations, public API changes or changes to
financial posting/inventory quantities. Advanced technical model labels retain
their existing wording. This review does not replace staging business-flow or
backup/restoration verification. See the offline VPS guide for installation;
merging a PR alone does not update the running container.
