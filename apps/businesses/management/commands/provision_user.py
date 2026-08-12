from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import User
from apps.businesses.entitlements import EntitlementError, require_seat_available, seats_remaining
from apps.businesses.models import Business, BusinessMembership
from apps.businesses.permissions import defaults_for_role
from apps.core.persian import normalize_phone


class Command(BaseCommand):
    help = (
        "Create a platform User and attach it to an existing Business. "
        "Authentication never creates accounts, so every User must be provisioned here first."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("--phone", required=True, help="Mobile, e.g. 09121234567")
        parser.add_argument("--business", required=True, help="Business slug or id")
        parser.add_argument("--full-name", default="")
        parser.add_argument(
            "--role",
            default=BusinessMembership.Role.STAFF,
            choices=[choice for choice, _label in BusinessMembership.Role.choices],
        )

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        phone = normalize_phone(options["phone"])
        if not (phone.startswith("09") and len(phone) == 11):
            raise CommandError(f"Not a valid Iranian mobile number: {options['phone']}")

        reference = options["business"]
        business = Business.objects.filter(slug=reference).first()
        if business is None:
            business = Business.objects.filter(id=reference).first() if _looks_like_uuid(reference) else None
        if business is None:
            raise CommandError(f"No business matches '{reference}'")

        if BusinessMembership.objects.filter(user__phone=phone, business=business).exists():
            raise CommandError(f"{phone} is already a member of {business.name}")

        try:
            require_seat_available(business)
        except EntitlementError as exc:
            raise CommandError(exc.message) from exc

        user, created = User.objects.get_or_create(
            phone=phone,
            defaults={"full_name": options["full_name"].strip()},
        )
        self.stdout.write(f"{'Created' if created else 'Reusing'} User {phone}")

        role = options["role"]
        BusinessMembership.objects.create(
            user=user,
            business=business,
            role=role,
            permissions=defaults_for_role(role),
            status=BusinessMembership.Status.ACTIVE,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Added {phone} to '{business.name}' as {role} "
                f"({seats_remaining(business)} of {business.seat_limit} seats left)"
            )
        )


def _looks_like_uuid(value: str) -> bool:
    import uuid

    try:
        uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return False
    return True
