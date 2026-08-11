from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import login
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone

from apps.core.persian import normalize_phone

from .models import OTPChallenge, User
from .sms import SmsMessage, get_sms_provider

logger = logging.getLogger(__name__)


class OTPError(Exception):
    """Base OTP domain error with a user-facing Persian message."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class OTPRateLimitError(OTPError):
    pass


class OTPValidationError(OTPError):
    pass


@dataclass(frozen=True)
class OTPRequestResult:
    phone: str
    expires_at: timezone.datetime
    cooldown_seconds: int
    dev_code: str | None = None


def _hash_code(code: str) -> str:
    secret = settings.SECRET_KEY
    return hashlib.sha256(f"{secret}:{code}".encode("utf-8")).hexdigest()


def _client_ip(request: HttpRequest | None) -> str | None:
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def request_login_otp(phone: str, *, request: HttpRequest | None = None) -> OTPRequestResult:
    phone = normalize_phone(phone)
    if not (phone.startswith("09") and len(phone) == 11):
        raise OTPValidationError("شماره موبایل معتبر نیست. مثال: ۰۹۱۲۳۴۵۶۷۸۹")

    now = timezone.now()
    cooldown = settings.OTP_REQUEST_COOLDOWN_SECONDS
    recent = (
        OTPChallenge.objects.filter(phone=phone, purpose=OTPChallenge.Purpose.LOGIN)
        .order_by("-created_at")
        .first()
    )
    if recent and (now - recent.created_at).total_seconds() < cooldown:
        raise OTPRateLimitError("لطفاً کمی صبر کنید و دوباره تلاش کنید.")

    hour_ago = now - timedelta(hours=1)
    hourly_count = OTPChallenge.objects.filter(
        phone=phone,
        purpose=OTPChallenge.Purpose.LOGIN,
        created_at__gte=hour_ago,
    ).count()
    if hourly_count >= settings.OTP_MAX_REQUESTS_PER_HOUR:
        raise OTPRateLimitError("تعداد درخواست‌های شما بیش از حد مجاز است. بعداً تلاش کنید.")

    code = "".join(secrets.choice("0123456789") for _ in range(settings.OTP_LENGTH))
    expires_at = now + timedelta(seconds=settings.OTP_EXPIRY_SECONDS)
    user_agent = ""
    if request is not None:
        user_agent = (request.META.get("HTTP_USER_AGENT") or "")[:255]

    with transaction.atomic():
        OTPChallenge.objects.create(
            phone=phone,
            code_hash=_hash_code(code),
            purpose=OTPChallenge.Purpose.LOGIN,
            max_attempts=settings.OTP_MAX_ATTEMPTS,
            expires_at=expires_at,
            request_ip=_client_ip(request),
            user_agent=user_agent,
        )

    body = f"کد ورود سنگا: {code}"
    try:
        get_sms_provider().send(SmsMessage(phone=phone, body=body))
    except Exception:
        logger.exception("Failed to send OTP SMS to %s", phone)
        raise OTPError("ارسال پیامک با مشکل روبه‌رو شد. دوباره تلاش کنید.") from None

    dev_code = code if settings.DEBUG and settings.SMS_PROVIDER == "console" else None
    return OTPRequestResult(
        phone=phone,
        expires_at=expires_at,
        cooldown_seconds=cooldown,
        dev_code=dev_code,
    )


def verify_login_otp(phone: str, code: str, *, request: HttpRequest) -> User:
    phone = normalize_phone(phone)
    code = (code or "").strip()
    if not code.isdigit():
        raise OTPValidationError("کد وارد شده معتبر نیست.")

    challenge = (
        OTPChallenge.objects.filter(
            phone=phone,
            purpose=OTPChallenge.Purpose.LOGIN,
            is_used=False,
        )
        .order_by("-created_at")
        .first()
    )
    if challenge is None:
        raise OTPValidationError("کد معتبری یافت نشد. دوباره درخواست دهید.")

    if challenge.is_expired:
        raise OTPValidationError("کد منقضی شده است. دوباره درخواست دهید.")

    if challenge.attempts >= challenge.max_attempts:
        raise OTPValidationError("تعداد تلاش‌ها تمام شده است. دوباره درخواست دهید.")

    if challenge.code_hash != _hash_code(code):
        challenge.attempts += 1
        challenge.save(update_fields=["attempts"])
        raise OTPValidationError("کد وارد شده نادرست است.")

    with transaction.atomic():
        challenge.is_used = True
        challenge.save(update_fields=["is_used"])
        user, _created = User.objects.get_or_create(phone=phone)
        if not user.is_active:
            raise OTPValidationError("حساب کاربری غیرفعال است.")
        login(request, user)
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])
    return user
