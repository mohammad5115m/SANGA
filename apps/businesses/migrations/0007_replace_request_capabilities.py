"""Replace request-era permissions with bilateral trade permissions.

Membership permissions are materialized JSON lists. Runtime aliases are not
enough: existing staff would otherwise lose the buying/selling controls when
the UI starts checking ``trade.propose`` and ``trade.confirm``.
"""

from django.db import migrations

FORWARD = {
    "purchase.request": "trade.propose",
    "sale.finalize": "trade.confirm",
}
BACKWARD = {value: key for key, value in FORWARD.items()}


def rewrite(apps, mapping):
    BusinessMembership = apps.get_model("businesses", "BusinessMembership")
    for membership in BusinessMembership.objects.all().iterator():
        rewritten: list[str] = []
        for code in membership.permissions or []:
            mapped = mapping.get(code, code)
            if mapped not in rewritten:
                rewritten.append(mapped)
        if rewritten != (membership.permissions or []):
            membership.permissions = rewritten
            membership.save(update_fields=["permissions"])


def forwards(apps, schema_editor):
    rewrite(apps, FORWARD)


def backwards(apps, schema_editor):
    rewrite(apps, BACKWARD)


class Migration(migrations.Migration):
    dependencies = [("businesses", "0006_verify_provisioned_businesses")]

    operations = [migrations.RunPython(forwards, backwards)]
