from __future__ import annotations

import uuid

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone

from .managers import UserManager


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    phone = models.CharField("شماره موبایل", max_length=20, unique=True)
    email = models.EmailField("ایمیل", blank=True)
    full_name = models.CharField("نام کامل", max_length=150, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)
    last_login = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        verbose_name = "کاربر"
        verbose_name_plural = "کاربران"
        ordering = ["-date_joined"]

    def __str__(self) -> str:
        return self.full_name or self.phone

    @property
    def display_name(self) -> str:
        return self.full_name or self.phone


class OTPChallenge(models.Model):
    class Purpose(models.TextChoices):
        #: Staff logging into a provisioned platform account.
        LOGIN = "login", "ورود"
        #: A retail customer confirming their phone when submitting an inquiry.
        #: Kept as a separate purpose so a code issued for one can never be
        #: replayed against the other — a customer verification must never
        #: become a session.
        CUSTOMER = "customer", "تأیید مشتری"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    phone = models.CharField(max_length=20, db_index=True)
    code_hash = models.CharField(max_length=128)
    purpose = models.CharField(max_length=20, choices=Purpose.choices, default=Purpose.LOGIN)
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    is_used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    request_ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "چالش OTP"
        verbose_name_plural = "چالش‌های OTP"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["phone", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"OTP {self.phone} ({self.purpose})"

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at


class OTPRequestThrottle(models.Model):
    """One row per thing being rate-limited, locked while it is checked.

    The request limits used to be three ``COUNT`` queries over ``OTPChallenge``
    followed — outside any transaction — by an ``INSERT``. Two requests arriving
    together both counted the same rows, both concluded they were under the
    limit, and both inserted. The cooldown, the per-phone cap and the per-IP cap
    could all be walked past simply by sending requests in parallel instead of in
    sequence, which is the easier thing to do.

    Counting rows was also the wrong shape for the job: it made the limit depend
    on a table that exists for a different reason and grows without bound. A
    counter that can be locked is both cheaper and correct.

    Not a distributed rate limiter. One row, one lock, one window — enough to
    hold under the concurrency a real deployment sees, and small enough to
    reason about.
    """

    class Scope(models.TextChoices):
        #: Keyed by phone *and* purpose: a customer verifying an inquiry must not
        #: spend the budget for a staff login to the same number.
        PHONE = "phone", "شماره"
        #: Keyed by source address across every purpose. Every other limit keys
        #: on the phone, so a caller working through a list of numbers would
        #: never touch one.
        ADDRESS = "address", "نشانی"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scope = models.CharField(max_length=20, choices=Scope.choices)
    key = models.CharField(max_length=120)

    #: Start of the current hour-long window. Rolled forward in place rather than
    #: by inserting a new row, so the table stays one row per active key.
    window_started_at = models.DateTimeField()
    count = models.PositiveIntegerField(default=0)
    last_request_at = models.DateTimeField()

    class Meta:
        verbose_name = "محدودیت درخواست کد"
        verbose_name_plural = "محدودیت‌های درخواست کد"
        constraints = [
            # The row *is* the lock, so there must be exactly one per key. Without
            # this two concurrent first-requests would each create their own and
            # neither would see the other's count.
            models.UniqueConstraint(fields=["scope", "key"], name="uniq_otp_throttle_key"),
        ]

    def __str__(self) -> str:
        return f"{self.scope}:{self.key} ({self.count})"
