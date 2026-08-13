"""Rewrite the materialized capability lists on every membership.

``BusinessMembership.permissions`` is a JSON list frozen at first save and never
recomputed, so renaming a capability code silently revokes access for every
existing member. Renaming without this migration is a data-loss bug that looks
like a refactor.

The mapping is duplicated here rather than imported from
``businesses.permissions``: a migration has to keep producing the same result
after the runtime constants change again.
"""

from django.db import migrations

# Old code -> new code. ``None`` means the capability is gone.
FORWARD = {
    # Named after the demand board, which no longer exists. They now describe
    # customer leads, which is what they were actually being used for.
    "inquiries.view": "leads.view",
    "inquiries.respond": "leads.manage",
    # Covered manual Contact CRUD, which the Business directory replaces.
    "customers.manage": "leads.manage",
    # Declared in v1 but never checked by any view or service.
    "analytics.view": None,
    "audit.view": None,
}

# New capabilities that did not exist before. Granted to members who already
# held the capability that used to imply them, so nobody loses a workflow they
# were doing yesterday.
IMPLIED = {
    # Anyone who could respond to demand-board offers was already the person
    # buying and selling.
    "inquiries.respond": ("purchase.request", "sale.finalize"),
    # Seeing the ledger already meant seeing what the business had sold.
    "ledger.view": ("invoice.view",),
    "ledger.manage": ("invoice.manage",),
}

BACKWARD = {
    "leads.view": "inquiries.view",
    "leads.manage": "inquiries.respond",
}

DROP_ON_BACKWARD = {"purchase.request", "sale.finalize", "invoice.view", "invoice.manage"}


def forwards(apps, schema_editor):
    BusinessMembership = apps.get_model("businesses", "BusinessMembership")

    for membership in BusinessMembership.objects.all().iterator():
        old = list(membership.permissions or [])
        new: list[str] = []

        for code in old:
            mapped = FORWARD.get(code, code)
            if mapped is not None and mapped not in new:
                new.append(mapped)
            for implied in IMPLIED.get(code, ()):
                if implied not in new:
                    new.append(implied)

        if new != old:
            membership.permissions = new
            membership.save(update_fields=["permissions"])


def backwards(apps, schema_editor):
    BusinessMembership = apps.get_model("businesses", "BusinessMembership")

    for membership in BusinessMembership.objects.all().iterator():
        new = [
            BACKWARD.get(code, code)
            for code in (membership.permissions or [])
            if code not in DROP_ON_BACKWARD
        ]
        deduped: list[str] = []
        for code in new:
            if code not in deduped:
                deduped.append(code)
        membership.permissions = deduped
        membership.save(update_fields=["permissions"])


class Migration(migrations.Migration):
    dependencies = [
        ("businesses", "0002_business_subscription"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
