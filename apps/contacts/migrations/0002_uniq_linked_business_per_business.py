from django.db import migrations, models


def unlink_duplicate_partner_links(apps, schema_editor):
    """Make existing data satisfy ``uniq_linked_business_per_business``.

    If a business already has several contacts pointing at the same partner, the
    partner's balance is already split. The oldest contact keeps the link (it is
    the one most likely to carry the history); the newer ones are unlinked. No
    contact and no ledger entry is deleted — only the optional pointer is cleared,
    so the owner can re-link deliberately afterwards.
    """
    Contact = apps.get_model('contacts', 'Contact')
    seen: set[tuple] = set()
    duplicates: list = []
    linked = (
        Contact.objects.filter(linked_business__isnull=False)
        .order_by('business_id', 'linked_business_id', 'created_at', 'id')
        .values_list('id', 'business_id', 'linked_business_id')
    )
    for contact_id, business_id, linked_business_id in linked.iterator():
        key = (business_id, linked_business_id)
        if key in seen:
            duplicates.append(contact_id)
        else:
            seen.add(key)
    if duplicates:
        Contact.objects.filter(id__in=duplicates).update(linked_business=None)


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(unlink_duplicate_partner_links, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='contact',
            constraint=models.UniqueConstraint(condition=models.Q(('linked_business__isnull', False)), fields=('business', 'linked_business'), name='uniq_linked_business_per_business'),
        ),
    ]
