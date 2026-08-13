"""Map old inquiry statuses/sources and give every inquiry a CustomerLead.

Three things happen, in order:

1. Statuses collapse from seven to four. The three that go — ``viewed``,
   ``negotiating``, ``lost`` — were never distinguishable in practice: sellers
   used «جدید» until they phoned and «بسته» afterwards.
2. Source codes are renamed to match the V2 surfaces.
3. Every existing inquiry gets a ``CustomerLead``, deduplicated by
   ``(business, phone)`` — which is the point of leads: the same customer asking
   twice should be one person, not two.

Creating a lead creates no platform User. A retail customer has no account.
"""

from django.db import migrations

STATUS_MAP = {
    "viewed": "new",          # seen but not acted on is still new work
    "negotiating": "contacted",
    "lost": "closed",
}

SOURCE_MAP = {
    "lot_detail": "item_detail",
    "share": "share_link",
}


def forwards(apps, schema_editor):
    Inquiry = apps.get_model("inquiries", "Inquiry")
    InquiryItem = apps.get_model("inquiries", "InquiryItem")
    CustomerLead = apps.get_model("inquiries", "CustomerLead")

    for old, new in STATUS_MAP.items():
        Inquiry.objects.filter(status=old).update(status=new)
    for old, new in SOURCE_MAP.items():
        Inquiry.objects.filter(source=old).update(source=new)

    leads: dict[tuple, object] = {}
    for inquiry in Inquiry.objects.select_related("lot", "lot__product").iterator():
        key = (inquiry.business_id, inquiry.phone)
        lead = leads.get(key)
        if lead is None:
            lead, _ = CustomerLead.objects.get_or_create(
                business_id=inquiry.business_id,
                phone=inquiry.phone,
                defaults={"name": inquiry.name or inquiry.phone},
            )
            leads[key] = lead

        inquiry.lead = lead
        inquiry.save(update_fields=["lead"])

        # Carry the single-product link onto a line, so old and new inquiries
        # render through the same template.
        if inquiry.lot_id and not InquiryItem.objects.filter(inquiry=inquiry).exists():
            InquiryItem.objects.create(
                inquiry=inquiry,
                item=inquiry.lot,
                product_name=inquiry.lot.product.commercial_name,
            )


def backwards(apps, schema_editor):
    """Leads and lines are additive; dropping them loses nothing the old schema had."""
    Inquiry = apps.get_model("inquiries", "Inquiry")
    InquiryItem = apps.get_model("inquiries", "InquiryItem")
    CustomerLead = apps.get_model("inquiries", "CustomerLead")

    InquiryItem.objects.all().delete()
    Inquiry.objects.update(lead=None)
    CustomerLead.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("inquiries", "0003_remove_inquiry_assignee_alter_inquiry_source_and_more"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
