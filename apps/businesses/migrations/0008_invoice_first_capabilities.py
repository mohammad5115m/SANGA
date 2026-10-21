"""Grant invoice-first capabilities without broadening viewer access."""

from django.db import migrations


INVOICE_FIRST = (
    "invoice.create",
    "invoice.send",
    "invoice.confirm",
    "invoice.offline_approve",
    "counterparty.local.manage",
    "counterparty.link.propose",
    "cheque.manage",
    "report.view",
)
MANAGER_ONLY = (
    "invoice.business_signature.manage",
    "counterparty.link.approve",
)


def forwards(apps, schema_editor):
    Membership = apps.get_model("businesses", "BusinessMembership")
    for membership in Membership.objects.all().iterator():
        permissions = list(membership.permissions or [])
        standard_salesperson = {"invoice.view", "trade.propose", "trade.confirm"}.issubset(permissions)
        if membership.role in {"owner", "manager"} or "invoice.manage" in permissions or standard_salesperson:
            for code in INVOICE_FIRST:
                if code not in permissions:
                    permissions.append(code)
        if membership.role in {"owner", "manager"}:
            for code in MANAGER_ONLY:
                if code not in permissions:
                    permissions.append(code)
        if permissions != (membership.permissions or []):
            membership.permissions = permissions
            membership.save(update_fields=["permissions"])


def backwards(apps, schema_editor):
    Membership = apps.get_model("businesses", "BusinessMembership")
    removed = set(INVOICE_FIRST + MANAGER_ONLY)
    for membership in Membership.objects.all().iterator():
        permissions = [code for code in (membership.permissions or []) if code not in removed]
        if permissions != (membership.permissions or []):
            membership.permissions = permissions
            membership.save(update_fields=["permissions"])


class Migration(migrations.Migration):
    dependencies = [("businesses", "0007_replace_request_capabilities")]
    operations = [migrations.RunPython(forwards, backwards)]
