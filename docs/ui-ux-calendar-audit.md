# UI, UX, and Persian calendar audit

This change reviewed the four shared layouts and 107 templates behind the 152 declared application routes, plus Django's registered administration screens. Routes were classified during the audit as rendered pages, redirects, HTMX fragments, JSON helpers, document downloads, or mutation endpoints; mutation endpoints were exercised through their owning flows rather than opened as pages.

| Area | Representative coverage | Result |
| --- | --- | --- |
| Authentication and onboarding | OTP login, profile, onboarding, no-business redirects | Verified |
| Dashboard and navigation | Seller, partner, and restricted staff navigation and permission redirects | Verified |
| Inventory and media | List, filters, create/edit/detail/delete, media upload empty state and gallery switching | Verified |
| Marketplace | Partner list/detail behavior and legacy request redirects | Verified |
| Catalog and storefront | Manage/create/edit, collections, shared item/catalog, guided storefront links, unavailable states | Verified |
| Customer inquiry and CRM | Public selection through OTP confirmation, inbox/detail, customers, notes and follow-ups | Verified |
| Invoices and settlement | List/filter, create/edit/detail/print, validation retry, row ordering, picker, preview and expansion | Verified |
| Trading | Agreements, sent/received history and finalized trade detail | Verified |
| Accounting and reports | Ledger, aging, statement/add/reverse/print and every report switcher entry | Verified |
| Notifications, settings and team | Page navigation, empty states and permission gates | Verified |
| Admin | 129 available index, list, add, detail and history screens across all registered models | Verified |
| Errors and offline | Product/catalog unavailable, custom 404/500 markup and offline page | Verified |

Responsive browser checks covered 1440x1000, 768x1024, 390x844 and 320x844 viewports. Tables remain in named, keyboard-reachable scrolling regions. Focus, Escape/return-focus behavior, HTMX back/forward restoration, persistent error feedback, long content, share feedback and dynamic invoice controls were checked with keyboard-accessible controls. No horizontal page overflow remained in application, storefront, or admin pages; document tables scroll within their own named region on narrow screens.

Dates now use `apps.core.calendar` as the single presentation and conversion service. Date-only values are converted without timezone shifts. Timestamps are localized through Django before Jalali formatting. Inputs accept Persian, Arabic, or Latin digits and retain canonical ISO hidden values for existing clients and storage. The shared widget provides a Saturday-first calendar and an explicit Tehran 24-hour time field. Admin lists, forms, inlines, history, and date ranges use the same services while ordering and permissions remain on the model fields.

The local font removes the page dependency on Google Fonts, reserves media dimensions, and embeds a cached font only in self-contained invoice exports. Invoice preview work is delayed on mobile until requested, aborted when replaced, and no longer creates one document listener per row. Existing query-budget tests still guard list pages against row-count-dependent query growth.

Verification completed locally: Ruff; Django checks; migration drift; fresh SQLite migration; focused JavaScript runtime and PWA refresh tests; Python regressions; production `collectstatic`; and browser flow checks. The full SQLite run completed with 898 passes and 22 expected PostgreSQL-only skips before the final focused fixes; affected suites then passed 137, 58, and 32 tests. Three PDF rendering tests could not run on this Windows host because its native Pango/GObject libraries are absent. The production image and PostgreSQL concurrency lanes are unavailable locally because Docker and PostgreSQL clients are not installed; CI supplies those dependencies on pull requests.
