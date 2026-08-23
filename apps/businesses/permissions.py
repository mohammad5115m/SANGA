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
TRADE_PROPOSE: Final = "trade.propose"
TRADE_CONFIRM: Final = "trade.confirm"

# Compatibility aliases for historical services and migrations. New UI and
# authorization code use the trade vocabulary above: purchase requests are now
# read-only history, and no colleague sale finalizes without the other party.
PURCHASE_REQUEST: Final = TRADE_PROPOSE
SALE_FINALIZE: Final = TRADE_CONFIRM

# --- invoicing ----------------------------------------------------------------
INVOICE_VIEW: Final = "invoice.view"
INVOICE_MANAGE: Final = "invoice.manage"
INVOICE_CREATE: Final = "invoice.create"
INVOICE_SEND: Final = "invoice.send"
INVOICE_CONFIRM: Final = "invoice.confirm"
INVOICE_OFFLINE_APPROVE: Final = "invoice.offline_approve"
BUSINESS_SIGNATURE_MANAGE: Final = "invoice.business_signature.manage"
LOCAL_COUNTERPARTY_MANAGE: Final = "counterparty.local.manage"
COUNTERPARTY_LINK_PROPOSE: Final = "counterparty.link.propose"
COUNTERPARTY_LINK_APPROVE: Final = "counterparty.link.approve"
CHEQUE_MANAGE: Final = "cheque.manage"
REPORT_VIEW: Final = "report.view"

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
    TRADE_PROPOSE,
    TRADE_CONFIRM,
    INVOICE_VIEW,
    INVOICE_MANAGE,
    INVOICE_CREATE,
    INVOICE_SEND,
    INVOICE_CONFIRM,
    INVOICE_OFFLINE_APPROVE,
    BUSINESS_SIGNATURE_MANAGE,
    LOCAL_COUNTERPARTY_MANAGE,
    COUNTERPARTY_LINK_PROPOSE,
    COUNTERPARTY_LINK_APPROVE,
    CHEQUE_MANAGE,
    REPORT_VIEW,
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
    TRADE_PROPOSE: "ثبت توافق معامله",
    TRADE_CONFIRM: "تأیید معامله",
    INVOICE_VIEW: "دیدن فاکتورها",
    INVOICE_MANAGE: "صدور و مدیریت فاکتور",
    INVOICE_CREATE: "ساخت و ویرایش پیش‌نویس فاکتور",
    INVOICE_SEND: "ارسال فاکتور همکار",
    INVOICE_CONFIRM: "تأیید یا رد فاکتور دریافتی",
    INVOICE_OFFLINE_APPROVE: "ثبت تأیید آفلاین همکار محلی",
    BUSINESS_SIGNATURE_MANAGE: "مدیریت امضای رسمی کسب‌وکار",
    LOCAL_COUNTERPARTY_MANAGE: "مدیریت همکاران محلی",
    COUNTERPARTY_LINK_PROPOSE: "پیشنهاد اتصال همکار محلی",
    COUNTERPARTY_LINK_APPROVE: "تأیید انتقال سابقه همکار محلی",
    CHEQUE_MANAGE: "مدیریت وضعیت چک‌ها",
    REPORT_VIEW: "مشاهده گزارش‌ها",
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
        TRADE_PROPOSE,
        TRADE_CONFIRM,
        INVOICE_VIEW,
        INVOICE_CREATE,
        INVOICE_SEND,
        INVOICE_CONFIRM,
        INVOICE_OFFLINE_APPROVE,
        LOCAL_COUNTERPARTY_MANAGE,
        COUNTERPARTY_LINK_PROPOSE,
        CHEQUE_MANAGE,
        REPORT_VIEW,
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
    "purchase.request": TRADE_PROPOSE,
    "sale.finalize": TRADE_CONFIRM,
}


def defaults_for_role(role: str) -> list[str]:
    return list(ROLE_DEFAULTS.get(role, ROLE_DEFAULTS["staff"]))


def label_for(capability: str) -> str:
    return CAPABILITY_LABELS.get(capability, capability)
