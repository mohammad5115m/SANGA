"""Record the approval that provisioning already represented.

Network eligibility now requires ``verification_status=VERIFIED``. Every existing
Business carries the ``unverified`` default, because nothing ever set the field —
which is exactly why the policy had to be a denylist. Without this backfill the
directory, the colleague marketplace and public search all go empty on deploy.

This exposes nothing. Every Business it touches is one that is already visible
today: active, not refused, and reachable from every discovery surface. The
backfill writes down a decision a platform admin made when they provisioned the
account, so that from here the policy can bind new tenants.

Deliberately narrow:

* ``status`` must be ACTIVE — a suspended tenant is not silently approved.
* REJECTED and SUSPENDED are left alone. Those are explicit refusals, and a
  migration that overturned one would re-publish a Business the platform had
  removed on purpose.
* PENDING is left alone. Something set it, which means somebody is meant to look.
"""

from __future__ import annotations

from django.db import migrations


def verify_existing(apps, schema_editor):
    Business = apps.get_model("businesses", "Business")
    Business.objects.filter(status="active", verification_status="unverified").update(
        verification_status="verified"
    )


def unverify(apps, schema_editor):
    """Put the field back the way it was.

    Reversible only in the sense that it restores the previous *value*. Anything
    verified through the admin after this ran is indistinguishable from anything
    verified by it, so reversing loses that distinction — which is why the
    forward direction is deliberately restricted to rows carrying the default.
    """
    Business = apps.get_model("businesses", "Business")
    Business.objects.filter(status="active", verification_status="verified").update(
        verification_status="unverified"
    )


class Migration(migrations.Migration):
    dependencies = [("businesses", "0005_business_invoice_sequence")]

    operations = [migrations.RunPython(verify_existing, unverify)]
