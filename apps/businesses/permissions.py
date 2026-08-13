"""What a *member* may do inside their Business.

Kept strictly separate from :mod:`apps.businesses.entitlements`, which answers
the different question of what the *Business* has paid for. Both are enforced
server-side, and both have to pass:

    plan says the business may publish  AND  membership says this user may publish

Capability codes are **materialized** onto ``BusinessMembership.permissions`` at
first save and never recomputed, so renaming a code silently revokes access for
every existing member. Any change here ships with a data migration that rewrites
the stored lists — see ``businesses.0003``.
"""

from __future__ import annotations

from typing import Final

# --- product ------------------------------------------------------------------
INVENTORY_VIEW: Final = "inventory.view"
INVENTORY_CREATE: Final = "inventory.create"
INVENTORY_EDIT: Final = "inventory.edit"
INVENTORY_QUANTITY: Final = "inventory.quantity"
INVENTORY_MEDIA: Final = "inventory.media"
INVENTORY_PUBLISH: Final = "inventory.publish"
INVENTORY_CONFIRM: Final = "inventory.confirm"

# --- pricing ------------------------------------------------------------------
PRICES_VIEW: Final = "prices.view"
PRICES_EDIT: Final = "prices.edit"

# --- buying and selling -------------------------------------------------------
PURCHASE_REQUEST: Final = "purchase.request"
SALE_FINALIZE: Final = "sale.finalize"

# --- invoicing ----------------------------------------------------------------
INVOICE_VIEW: Final = "invoice.view"
INVOICE_MANAGE: Final = "invoice.manage"

# --- money --------------------------------------------------------------------
LEDGER_VIEW: Final = "ledger.view"
LEDGER_MANAGE: Final = "ledger.manage"

# --- customers ----------------------------------------------------------------
LEADS_VIEW: Final = "leads.view"
LEADS_MANAGE: Final = "leads.manage"

# --- other --------------------------------------------------------------------
CATALOG_MANAGE: Final = "catalog.manage"
TEAM_MANAGE: Final = "team.manage"
BUSINESS_SETTINGS: Final = "business.settings"

ALL_CAPABILITIES: Final[tuple[str, ...]] = (
    INVENTORY_VIEW,
    INVENTORY_CREATE,
    INVENTORY_EDIT,
    INVENTORY_QUANTITY,
    INVENTORY_MEDIA,
    INVENTORY_PUBLISH,
    INVENTORY_CONFIRM,
    PRICES_VIEW,
    PRICES_EDIT,
    PURCHASE_REQUEST,
    SALE_FINALIZE,
    INVOICE_VIEW,
    INVOICE_MANAGE,
    LEDGER_VIEW,
    LEDGER_MANAGE,
    LEADS_VIEW,
    LEADS_MANAGE,
    CATALOG_MANAGE,
    TEAM_MANAGE,
    BUSINESS_SETTINGS,
)

#: Persian labels for the team-management screen. A capability nobody can
#: describe in one phrase is a capability nobody will assign correctly.
CAPABILITY_LABELS: Final[dict[str, str]] = {
    INVENTORY_VIEW: "دیدن محصولات",
    INVENTORY_CREATE: "ثبت محصول جدید",
    INVENTORY_EDIT: "ویرایش محصول",
    INVENTORY_QUANTITY: "تغییر مقدار موجودی",
    INVENTORY_MEDIA: "مدیریت عکس و ویدیو",
    INVENTORY_PUBLISH: "انتشار و توقف انتشار",
    INVENTORY_CONFIRM: "تأیید موجودی",
    PRICES_VIEW: "دیدن قیمت‌ها",
    PRICES_EDIT: "تغییر قیمت‌ها",
    PURCHASE_REQUEST: "ارسال درخواست خرید",
    SALE_FINALIZE: "نهایی کردن فروش",
    INVOICE_VIEW: "دیدن فاکتورها",
    INVOICE_MANAGE: "صدور و مدیریت فاکتور",
    LEDGER_VIEW: "دیدن دفتر حساب",
    LEDGER_MANAGE: "ثبت سند مالی",
    LEADS_VIEW: "دیدن استعلام مشتریان",
    LEADS_MANAGE: "پاسخ به استعلام مشتریان",
    CATALOG_MANAGE: "مدیریت کاتالوگ‌ها",
    TEAM_MANAGE: "مدیریت تیم",
    BUSINESS_SETTINGS: "تنظیمات کسب‌وکار",
}

ROLE_DEFAULTS: Final[dict[str, tuple[str, ...]]] = {
    "owner": ALL_CAPABILITIES,
    "manager": ALL_CAPABILITIES,
    # A salesperson runs the day-to-day: products, prices, customers, sales.
    # They cannot post financial entries or change who is on the team.
    "staff": (
        INVENTORY_VIEW,
        INVENTORY_CREATE,
        INVENTORY_EDIT,
        INVENTORY_QUANTITY,
        INVENTORY_MEDIA,
        INVENTORY_PUBLISH,
        INVENTORY_CONFIRM,
        PRICES_VIEW,
        PURCHASE_REQUEST,
        SALE_FINALIZE,
        INVOICE_VIEW,
        LEADS_VIEW,
        LEADS_MANAGE,
        CATALOG_MANAGE,
        LEDGER_VIEW,
    ),
    "viewer": (
        INVENTORY_VIEW,
        LEADS_VIEW,
    ),
}

#: Old code -> new code, applied by the data migration in ``businesses.0003``.
#: A value of ``None`` means the capability is gone and should be dropped.
#:
#: ``inquiries.*`` were named after the demand board, which no longer exists;
#: they now describe customer leads. ``customers.manage`` covered manual Contact
#: CRUD, which the Business directory replaces. ``analytics.view`` and
#: ``audit.view`` were declared but never checked anywhere.
CAPABILITY_MIGRATION_MAP: Final[dict[str, str | None]] = {
    "inquiries.view": LEADS_VIEW,
    "inquiries.respond": LEADS_MANAGE,
    "customers.manage": LEADS_MANAGE,
    "analytics.view": None,
    "audit.view": None,
}


def defaults_for_role(role: str) -> list[str]:
    return list(ROLE_DEFAULTS.get(role, ROLE_DEFAULTS["staff"]))


def label_for(capability: str) -> str:
    return CAPABILITY_LABELS.get(capability, capability)
