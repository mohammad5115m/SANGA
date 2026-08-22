# SANGA complete UI/UX enhancement — implementation report

Date: 2026-08-22  
Working branch: `agent/sanga-complete-ui-ux-enhancement`  
Base branch: `agent/sanga-v2-product-catalog-simplification`
Base commit: `237194bf6060b12f41bd3b65ef1077f4776eb5f5`
Remote implementation commit: `00b373bf92725c40c3d2d49f5d9b30a4bc5f9a1a feat: complete SANGA UI/UX enhancement`

## 1. Executive summary

SANGA already had a sound domain model, server-enforced permissions, tenant-scoped selectors, and a compact Persian RTL interface. The main quality gap was not missing business functionality; it was uneven interaction reliability and presentation across otherwise complete workflows. The most consequential problems were a CSP-blocked inquiry removal interaction, CSP-blocked print buttons, a public product-card grid whose selection controls became unintended grid children, dead-end product creation prompts, a desktop content column constrained to 720 pixels, and a mouse-only asynchronous product picker.

The implemented direction is a restrained, stone-neutral operational interface: warm neutral surfaces, deep green structure, bronze emphasis, stronger typographic and spacing hierarchy, larger touch targets, consistent focus treatment, and wider desktop work areas. Existing Django, HTMX, permission, pricing, tenancy, PWA, and workflow architecture was preserved.

The highest-impact improvements are:

- Reliable CSP-safe inquiry removal and print actions.
- Permission- and plan-aware creation prompts on the dashboard and navigation.
- Correct product-card composition on public storefront, catalog, search, and B2B marketplace grids.
- A grouped one-page product form with required/optional indicators, progressive disclosure, clear units, unsaved-change protection, and duplicate-submit prevention.
- A keyboard-accessible ARIA combobox product picker with loading and error feedback.
- Clear active navigation, skip navigation, global focus states, live HTMX status feedback, table semantics, and reduced-motion support.
- Actionable dashboard price-attention links backed by a tested owner-side `needs_price` filter.
- Consistent centimetre presentation at UI boundaries while preserving the internal millimetre model.
- Safer confirmation copy for irreversible trade, invoice, and financial actions.

## 2. Audit findings

| Severity | Page or workflow | Problem | User impact | Resolution | Status |
|---|---|---|---|---|---|
| High | Public multi-product inquiry review | A form was nested inside another form and removal depended on inline `onclick`, which the CSP blocks. | Removing a selected item was unreliable and the DOM was invalid. | Replaced the nested form with one submit button using `formaction` and the outer CSRF token. | Implemented and contract-tested |
| High | Invoice, statement, and report printing | Print controls used inline JavaScript blocked by `script-src 'self'`. | The visible print action could do nothing. | Kept the redesigned invoice document's external `data-print-invoice` workflow and added CSP-safe `data-print-action` controls for statements and reports. | Implemented and contract-tested |
| High | Public and marketplace product grids | The card and its selection form were separate grid children; special and urgent badges overlapped. | Cards and actions became misaligned, especially on mobile and shared catalogs. | Added a single semantic card wrapper and grouped flags; catalog notes now remain inside the same grid item. | Implemented |
| High | Dashboard and mobile navigation | Product creation was offered to roles or plans unable to finish the workflow. | Users entered a dead end and encountered a later permission refusal. | Gated dashboard actions, empty-state CTA, and quick-add navigation on `can_add_products`; added viewer coverage. | Implemented and tested |
| High | Product, invoice, catalog, and trade forms | Long forms had no accidental-navigation warning and submit protection only worked when a browser exposed `event.submitter`. | Entered data could be lost and repeated writes were easier to trigger. | Added reusable unsaved-form hooks, link/beforeunload warnings, robust form-level duplicate guards, and pending button state. | Implemented |
| High | Invoice and trade product selector | The asynchronous picker was mouse-oriented and lacked combobox semantics or keyboard control. | Keyboard and assistive-technology users could not select products reliably. | Added combobox/listbox ARIA, active descendant handling, arrows, Enter, Escape, busy state, focus recovery, and live feedback. | Implemented and contract-tested |
| High | Trade proposal and invoice lifecycle | Finalize, reject, cancel, and issue-adjacent consequences were not always explicit at the action point. | Users could make consequential financial/workflow changes without enough warning. | Added targeted consequence copy and confirmations to irreversible actions; reversible actions remain direct. | Implemented |
| Medium | Desktop authenticated shell | The main operational column was capped at 720 px. | Tables, dashboards, and multi-field forms were unnecessarily cramped on desktop. | Introduced an 1180 px content token while retaining responsive padding and mobile stacking. | Implemented |
| Medium | Search and filtering | Result count and applied-filter state were not prominent. | Users could not quickly tell what the current result set represented. | Added a search landmark, live result count, active-filter count, and clearer expanded-filter label. | Implemented |
| Medium | Dashboard price-attention metric | The metric linked to the unfiltered inventory list and omitted products with no price from its displayed count. | The number did not lead directly to the work it described. | Added a tested owner-only `needs_price` filter for missing or stale prices and linked the combined count to it. | Implemented and tested |
| Medium | Public comparison and trade request | Internal `thickness_mm` values appeared without a clear display unit; comparison showed a raw nullable stock value. | Customers could misread dimensions and unavailable/inquiry stock states. | Switched display boundaries to `thickness_cm` with explicit centimetres and used the existing stock-state label. | Implemented and contract-tested |
| Medium | Global navigation and system status | Active links lacked `aria-current`; there was no skip link or global asynchronous announcement. | Current location and route changes were less clear to keyboard and screen-reader users. | Added `aria-current`, a skip link, focusable main regions, a route progress indicator, and a live status region. | Implemented |
| Medium | Tables and print documents | Header cells did not declare column/row scope; mobile overflow and print pagination were inconsistent. | Financial and report tables were harder to navigate and could clip. | Added explicit scopes, corrected total cells, standardized scroll wrappers, numeric isolation, A4 rules, and repeating print headers/footers. | Implemented and contract-tested |
| Medium | One-page product form | Fields were visually flat and the distinction between identity, stock, descriptions, publication, B2B, and B2C pricing was weak. | Sellers had to infer grouping and requiredness in a high-frequency workflow. | Added a reusable field component, logical sections, progressive disclosure, requirement markers, unit guidance, and sticky mobile actions. | Implemented and rendered |
| Low | Persian copy | Search terminology varied between `جستجو`, `جستجوی`, and half-space forms. | The product felt less consistent and polished. | Standardized changed user-facing search copy to `جست‌وجو`/`جست‌وجوی`. | Implemented |

No B2B-price exposure was found on public templates or response paths. Existing public/B2B separation, tenant isolation, ledger idempotency, and entitlement tests remained intact and passed.

## 3. Implemented changes

### Application shell and navigation

- Added skip navigation, focusable main regions, active-page semantics, larger touch targets, responsive bottom-navigation columns, and clearer mobile header behavior.
- Added route progress and screen-reader announcements for HTMX navigation and failures.
- Preserved server-side capability and plan enforcement; UI gating now matches those rules more closely.

### Dashboard

- Hid product creation prompts when the current membership/plan cannot create.
- Made the price-attention metric include missing and stale prices and link to a matching inventory filter.
- Kept the existing operational-first dashboard ordering and query-bounded data architecture.

### Products and inventory

- Reorganized the single product form into identity, specification/inventory, optional descriptions, publication, B2B pricing, and B2C pricing.
- Added required/optional markers, unit guidance, error focus, accidental-loss protection, and responsive/sticky actions.
- Fixed product-card grid composition, missing-image semantics, multi-flag overlap, and shared-catalog note placement.
- Preserved price tier selection in backend presenters; the product card still receives only the already-authorized display price.

### Search and marketplace

- Added search landmarks, result/filter summaries, clearer reset behavior, and consistent Persian terminology.
- Added a fully keyboard-operable asynchronous product selector.
- Corrected public-facing thickness and stock presentation.

### Public storefront and catalogs

- Corrected public card/action layout without adding login gates or moving customer identification earlier.
- Kept selection and catalog notes attached to their cards.
- Added robust copy-link fallback and polite success/error feedback.
- Kept B2B fields out of public markup, scripts, and data attributes.

### Inquiries

- Removed invalid nested forms and CSP-blocked inline behavior from inquiry review.
- Preserved quantity editing, removal, CSRF protection, and the identify/verify/submit workflow.

### Trading

- Added explicit confirmations for proposal confirmation/finalization, rejection, cancellation, and other consequential submissions.
- Added unsaved-change and duplicate-submit protection to proposal and direct-sale forms.
- Preserved transaction idempotency and the existing invoice/ledger creation services.

### Invoices

- Integrated with the newer invoice editor/document system already present on the latest base instead of restoring the removed legacy invoice partials.
- Added unsaved-change and duplicate-submit protection without taking ownership away from the invoice editor's dynamic product picker.
- Added semantic scopes to the standalone invoice document and preserved its CSP-safe external print control.
- Strengthened the cancellation action's destructive visual treatment while retaining explicit consequence copy.

### Accounting

- Added table header scopes and row-total semantics to aging and statement views.
- Improved mobile overflow, numeric isolation, and A4 print behavior.
- Kept debit/credit labels alongside color so meaning does not depend on color alone.

### Reports, settings, and team

- Standardized report-table semantics, numeric presentation, scrolling, and print behavior.
- Audited team, settings, restricted business, suspended business, and entitlement-controlled navigation. Existing backend gates and explanatory restricted-state banners were preserved.

### Accessibility

- Added global `:focus-visible` treatment, skip navigation, main landmarks, `aria-current`, live regions, explicit table scopes, combobox/listbox semantics, keyboard control, and reduced-motion fallbacks.
- Added static regression contracts for inline handlers, table scopes, public units, print controls, inquiry form structure, and global interaction hooks.

### Responsive design

- Widened desktop operational content while retaining mobile-first stacking.
- Added 44–46 px primary touch targets, compact phone header rules, overflow-contained tables, auto-fitting bottom navigation, resilient long-text wrapping, and sticky form actions above the safe-area-aware bottom navigation.

### Design system

- Refined the existing neutral/green/bronze token palette and border contrast.
- Added reusable requirement labels, form grids/sections/disclosures, loading/busy states, card wrappers, filter metadata, and print rules.

### Performance

- Kept animations small and disabled them for reduced-motion preferences.
- Prevented repeated JavaScript initialization across HTMX body swaps.
- Used `Exists` subqueries only when the explicit owner-side price-attention filter is selected.
- Added no remote runtime dependency and preserved lazy image loading.

## 4. Changed files

| File | Purpose |
|---|---|
| `apps/businesses/tests/test_navigation.py` | Verifies viewers are not offered product creation. |
| `apps/core/tests/test_ui_contracts.py` | Adds static CSP, accessibility, unit, print, and form-structure contracts. |
| `apps/inventory/forms.py` | Standardizes the Persian search label. |
| `apps/inventory/selectors.py` | Adds the owner-only stale/missing price attention filter. |
| `apps/inventory/tests/test_product_hardening.py` | Verifies the price attention filter returns exactly fresh/stale/missing states expected. |
| `static/css/app.css` | Adds/refines tokens, layout width, accessibility, responsive, cards, forms, tables, busy/loading, and print styles. |
| `static/js/app.js` | Adds CSP-safe print/copy behavior, HTMX-safe initialization, live feedback, keyboard product picker, unsaved warnings, confirmations, and duplicate-submit prevention. |
| `templates/components/form_field.html` | Introduces the reusable accessible bound-field presentation. |
| `templates/base.html` | Adds skip link, route progress, and live status region. |
| `templates/layouts/app_shell.html` | Adds active-page semantics, main target, and permission-aware responsive navigation behavior. |
| `templates/layouts/auth_shell.html` | Uses the shared main-content target. |
| `templates/layouts/onboarding_shell.html` | Uses the shared focusable main-content target. |
| `templates/layouts/storefront_shell.html` | Uses the shared main-content target and consistent search wording. |
| `templates/businesses/dashboard.html` | Gates creation prompts and makes price attention actionable. |
| `templates/businesses/colleague_list.html` | Standardizes search wording. |
| `templates/inventory/product_form.html` | Rebuilds the one-page form hierarchy and behavior hooks. |
| `templates/inventory/_price_fields.html` | Reuses the new field component for pricing. |
| `templates/inventory/_filter_bar.html` | Adds search semantics, result count, and active-filter visibility. |
| `templates/inventory/_product_card.html` | Fixes card composition, flags, fallback semantics, selection, and notes. |
| `templates/inventory/lot_confirm_delete.html` | Standardizes public-search wording. |
| `templates/marketplace/lot_detail.html` | Displays thickness in centimetres. |
| `templates/catalog/public_search.html` | Standardizes Persian public-search copy. |
| `templates/catalog/compare.html` | Uses public stock labels and centimetre thickness. |
| `templates/catalog/inquiry_review.html` | Removes nested forms/inline JS and adds unsaved-change protection. |
| `templates/catalog/inquiry_done.html` | Standardizes search copy. |
| `templates/catalog/item_unavailable.html` | Standardizes recovery-action copy. |
| `templates/catalog/manage_form.html` | Adds unsaved-change protection. |
| `templates/catalog/shared_catalog.html` | Keeps catalog notes inside the reusable card wrapper. |
| `templates/inquiries/inbox.html` | Standardizes search wording. |
| `templates/inquiries/lead_list.html` | Standardizes search wording. |
| `templates/trading/request_form.html` | Uses centimetre thickness display. |
| `templates/trading/proposal_form.html` | Adds unsaved-change protection. |
| `templates/trading/direct_sale.html` | Adds unsaved-change protection. |
| `templates/trading/proposal_detail.html` | Adds table semantics and consequential-action confirmations. |
| `templates/trading/trade_detail.html` | Adds table semantics and correct total-cell structure. |
| `templates/trading/sent_list.html` | Standardizes marketplace search wording. |
| `templates/invoicing/form.html` | Adds unsaved-change protection. |
| `templates/invoicing/detail.html` | Strengthens the destructive cancellation action while preserving its consequence confirmation. |
| `templates/invoicing/list.html` | Standardizes search wording. |
| `templates/invoicing/document.html` | Adds accessible column scopes to the redesigned standalone invoice document. |
| `templates/accounting/aging.html` | Adds accessible table scopes. |
| `templates/accounting/statement.html` | Adds accessible header and row-total scopes. |
| `templates/accounting/statement_print.html` | Adds table scopes and CSP-safe printing. |
| `templates/reporting/_body.html` | Adds report table scopes and correct row-total cells. |
| `templates/reporting/print.html` | Replaces CSP-blocked inline printing. |

## 5. Verification evidence

### Commands and results

The table below records the complete local validation of the UI/UX implementation before it was overlaid onto the newer invoice-system revision. The final integrated branch is validated by the pull request's GitHub Actions checks, which are the canonical verification for the exact remote tree.

| Check | Result |
|---|---|
| `DJANGO_DATABASE=sqlite .venv/bin/python manage.py check` | Passed; 0 issues. |
| `DJANGO_DATABASE=sqlite .venv/bin/python manage.py makemigrations --check --dry-run` | Passed; no changes detected. |
| `PATH="$PWD/.venv/bin:$PATH" bash scripts/check_fresh_migrate.sh` | Passed against a new temporary SQLite database. The downloaded snapshot did not retain executable mode, so the documented script was invoked with `bash`. |
| `.venv/bin/ruff check .` | Passed. |
| `node --check static/js/app.js` | Passed. |
| `DJANGO_DATABASE=sqlite .venv/bin/pytest -o addopts='' -q` | 803 passed, 22 PostgreSQL-only tests skipped on SQLite; 825 total, 0 failed. |
| Focused UI/navigation/inventory tests | 33 passed. |
| UI contract tests after final edits | 6 passed. |
| `git diff --check` | Passed. |

### Render and workflow checks

- Seeded the documented demo data and rendered authenticated routes for dashboard, inventory list, product creation, B2B marketplace, catalog management, trade proposals, invoices, accounting, and reports: all returned HTTP 200.
- Rendered public search and health endpoints: HTTP 200. Home correctly redirected (302), and the intentional offline page returned 503.
- Rendered the product form and confirmed the new section, required/optional, and unsaved-form hooks are present.
- Confirmed the response CSP remains restrictive for scripts (`script-src 'self'`) and the changed interactions no longer require inline handlers.
- Existing invoice-print and historical statement-print tests passed in the full suite; report print templates were rendered by report coverage and protected by UI contracts.
- Existing catalog/public-surface suites passed, including public eligibility and B2B/B2C price separation.
- Existing trade idempotency, multi-line sale, purchase flow, proposal, accounting, and invoice lifecycle suites passed.
- Permission/navigation coverage includes owner, manager/staff capability paths, viewer, browse/seller entitlement states, suspended/restricted businesses, and anonymous public customers through the existing and added suites.

### Accessibility, responsive, and browser evidence

- Source/static checks cover focus targets, skip navigation, ARIA current page, table scopes, no inline handlers, public unit boundaries, keyboard hooks, unsaved-form hooks, and duplicate-submit state.
- Responsive CSS was checked for phone (`max-width: 520px`), mobile/tablet (below 900 px), and desktop (900 px and above) behavior. No claim of pixel-level viewport verification is made because a browser binary was unavailable.
- A real browser/console, keyboard-only traversal, automated accessibility tree/axe run, and print-preview inspection could not be completed in this environment. Playwright was present, but no Chromium executable was installed; the attempted browser download was blocked by the runtime cache/network restrictions.
- Before/after screenshots: none captured for the same environment reason. No screenshots were fabricated.

## 6. Remaining issues

| Severity | Issue | Why it remains | Recommended next action |
|---|---|---|---|
| Medium (verification gap) | Pixel-level responsive, RTL, keyboard, browser-console, and print-preview QA plus required before/after screenshots. | No supported browser executable was available, and browser installation was blocked by the runtime environment. | Run Playwright/Chromium in CI or a developer environment at representative phone, tablet, and desktop widths; capture authenticated owner/viewer and anonymous public flows and inspect print preview. |
| Low (environment) | PostgreSQL-specific concurrency tests did not execute in the SQLite lane. | No local PostgreSQL service was available. | Run the existing PostgreSQL CI lane before merge. |
| Low (development warning) | Django emits a warning that `staticfiles/` has not been collected. | This is expected in the uncollected local development snapshot and does not affect Django test assertions. | Run `collectstatic` in the deployment/build lane as already documented. |

No unresolved critical or high-severity UI issue identified by this audit remains in the implementation.

## 7. Git summary

Remote implementation commit:

```text
00b373bf92725c40c3d2d49f5d9b30a4bc5f9a1a feat: complete SANGA UI/UX enhancement
Parent: 237194bf6060b12f41bd3b65ef1077f4776eb5f5
```

The destination branch was created directly from the latest base commit, then advanced with a normal fast-forward update. No force push, merge, branch deletion, or base-branch modification was performed. This report is committed separately so code and audit evidence remain independently reviewable.
