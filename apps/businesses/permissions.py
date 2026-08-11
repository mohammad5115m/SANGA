from __future__ import annotations

from typing import Final

# Capability codes documented in docs/permissions.md
INVENTORY_VIEW: Final = "inventory.view"
INVENTORY_CREATE: Final = "inventory.create"
INVENTORY_EDIT: Final = "inventory.edit"
INVENTORY_QUANTITY: Final = "inventory.quantity"
INVENTORY_MEDIA: Final = "inventory.media"
INVENTORY_PUBLISH: Final = "inventory.publish"
INVENTORY_CONFIRM: Final = "inventory.confirm"
PRICES_VIEW: Final = "prices.view"
PRICES_EDIT: Final = "prices.edit"
INQUIRIES_VIEW: Final = "inquiries.view"
INQUIRIES_RESPOND: Final = "inquiries.respond"
RESERVATIONS_VIEW: Final = "reservations.view"
RESERVATIONS_MANAGE: Final = "reservations.manage"
PARTNERS_MANAGE: Final = "partners.manage"
CUSTOMERS_MANAGE: Final = "customers.manage"
CATALOG_MANAGE: Final = "catalog.manage"
TEAM_MANAGE: Final = "team.manage"
BUSINESS_SETTINGS: Final = "business.settings"
ANALYTICS_VIEW: Final = "analytics.view"
AUDIT_VIEW: Final = "audit.view"

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
    INQUIRIES_VIEW,
    INQUIRIES_RESPOND,
    RESERVATIONS_VIEW,
    RESERVATIONS_MANAGE,
    PARTNERS_MANAGE,
    CUSTOMERS_MANAGE,
    CATALOG_MANAGE,
    TEAM_MANAGE,
    BUSINESS_SETTINGS,
    ANALYTICS_VIEW,
    AUDIT_VIEW,
)

ROLE_DEFAULTS: Final[dict[str, tuple[str, ...]]] = {
    "owner": ALL_CAPABILITIES,
    "manager": ALL_CAPABILITIES,
    "staff": (
        INVENTORY_VIEW,
        INVENTORY_CREATE,
        INVENTORY_EDIT,
        INVENTORY_QUANTITY,
        INVENTORY_MEDIA,
        INVENTORY_PUBLISH,
        INVENTORY_CONFIRM,
        PRICES_VIEW,
        INQUIRIES_VIEW,
        INQUIRIES_RESPOND,
        RESERVATIONS_VIEW,
        RESERVATIONS_MANAGE,
        CUSTOMERS_MANAGE,
        CATALOG_MANAGE,
    ),
    "viewer": (
        INVENTORY_VIEW,
        ANALYTICS_VIEW,
        INQUIRIES_VIEW,
        RESERVATIONS_VIEW,
    ),
}


def defaults_for_role(role: str) -> list[str]:
    return list(ROLE_DEFAULTS.get(role, ROLE_DEFAULTS["staff"]))
