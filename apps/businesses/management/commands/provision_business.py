from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import User
from apps.businesses.services import BusinessServiceError, create_business_for_owner
from apps.core.persian import normalize_phone


class Command(BaseCommand):
    help = (
        "Provision a Business together with its Owner User. "
        "SANGA has no self-service signup, so this command (or Django admin) is "
        "the only way a Business comes into existence."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("--name", required=True, help="Business name")
        parser.add_argument("--owner-phone", required=True, help="Owner mobile, e.g. 09121234567")
        parser.add_argument("--owner-name", default="", help="Owner full name")
        parser.add_argument("--city", default="")
        parser.add_argument("--province", default="")
        parser.add_argument("--phone", default="", help="Business phone; defaults to the owner's")

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        phone = normalize_phone(options["owner_phone"])
        if not (phone.startswith("09") and len(phone) == 11):
            raise CommandError(f"Not a valid Iranian mobile number: {options['owner_phone']}")

        owner, created = User.objects.get_or_create(
            phone=phone,
            defaults={"full_name": options["owner_name"].strip()},
        )
        if created:
            self.stdout.write(f"Created User {phone}")
        else:
            self.stdout.write(f"Reusing existing User {phone}")
            if options["owner_name"].strip() and not owner.full_name:
                owner.full_name = options["owner_name"].strip()
                owner.save(update_fields=["full_name"])

        try:
            business = create_business_for_owner(
                owner=owner,
                name=options["name"],
                city=options["city"],
                province=options["province"],
                phone=options["phone"],
            )
        except BusinessServiceError as exc:
            raise CommandError(exc.message) from exc

        self.stdout.write(
            self.style.SUCCESS(f"Provisioned business '{business.name}' (id={business.id}) owned by {phone}")
        )
