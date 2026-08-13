from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import login
from django.db import transaction
from django.db.models import F
from django.http import HttpRequest
from django.utils import timezone

from apps.core.persian import normalize_digits, normalize_phone

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


# Shown when the phone has no provisioned User, or has one that is deactivated.
# Both cases must produce the *same* message: telling an anonymous caller which
# of the two applies would confirm whether a number belongs to a SANGA account.
NOT_PROVISIONED_MESSAGE = "ورود با این شماره ممکن نیست. برای ایجاد حساب با پشتیبانی سنگا تماس بگیرید."


@dataclass(frozen=True)
class OTPRequestResult:
    phone: str
    expires_at: timezone.datetime
    cooldown_seconds: int
    dev_code: str | None = None


def _hash_code(code: str) -> str:
    secret = settings.SECRET_KEY
    return hashlib.sha256(f"{secret}:{code}".encode()).hexdigest()


def _client_ip(request: HttpRequest | None) -> str | None:
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _enforce_request_limits(*, phone: str, purpose: str, request: HttpRequest | None) -> None:
    """Cooldown, per-phone hourly cap, and a per-IP cap across both purposes.

    The per-IP cap is the one that was missing. Everything else keys on the phone
    number, so a caller with a list of numbers could request a code for each in
    turn and never touch a limit — SANGA pays the gateway for every one of them,
    and every recipient gets an unexplained message. The IP is not a strong
    identity, but a limit that costs an attacker a proxy per hundred messages is
    worth far more than no limit at all.

    Challenge rows are written for unprovisioned phones too, so these counters
    behave identically whether or not the number belongs to an account. Skipping
    them would turn the rate limiter itself into a phone-enumeration oracle.
    """
    now = timezone.now()
    hour_ago = now - timedelta(hours=1)

    recent = (
        OTPChallenge.objects.filter(phone=phone, purpose=purpose).order_by("-created_at").first()
    )
    if recent and (now - recent.created_at).total_seconds() < settings.OTP_REQUEST_COOLDOWN_SECONDS:
        raise OTPRateLimitError("لطفاً کمی صبر کنید و دوباره تلاش کنید.")

    per_phone = OTPChallenge.objects.filter(
        phone=phone, purpose=purpose, created_at__gte=hour_ago
    ).count()
    if per_phone >= settings.OTP_MAX_REQUESTS_PER_HOUR:
        raise OTPRateLimitError("تعداد درخواست‌های شما بیش از حد مجاز است. بعداً تلاش کنید.")

    ip = _client_ip(request)
    if ip:
        per_ip = OTPChallenge.objects.filter(request_ip=ip, created_at__gte=hour_ago).count()
        if per_ip >= settings.OTP_MAX_REQUESTS_PER_IP_PER_HOUR:
            logger.warning("OTP request rate limit hit for one source address")
            raise OTPRateLimitError("تعداد درخواست‌های شما بیش از حد مجاز است. بعداً تلاش کنید.")


def _claim_challenge(*, phone: str, purpose: str, code: str) -> OTPChallenge:
    """Consume a one-time code, or record the failed attempt. Serialized.

    Read-check-write with no lock let two requests carrying the same correct code
    both pass: each read ``is_used=False``, each concluded it had won, and a code
    that is one-time by definition was used twice. Wrong guesses had the mirror
    problem — concurrent attempts read the same counter and wrote back the same
    increment, so the attempt limit could be walked past by simply guessing in
    parallel.

    The row is locked for the whole decision, the attempt counter moves with an
    ``F()`` expression rather than a value read earlier, and the burn is a
    conditional update so only the transaction that actually flips ``is_used``
    is allowed to treat the code as spent.

    The refusal is raised *after* the transaction closes, never inside it. An
    exception thrown from within would roll the block back and take the attempt
    increment with it, so every wrong guess would be free and the attempt limit
    would never be reached.
    """
    refusal: str | None = None
    challenge: OTPChallenge | None = None

    with transaction.atomic():
        challenge = (
            OTPChallenge.objects.select_for_update()
            .filter(phone=phone, purpose=purpose, is_used=False)
            .order_by("-created_at")
            .first()
        )
        if challenge is None:
            refusal = "کد معتبری یافت نشد. دوباره درخواست دهید."
        elif challenge.is_expired:
            refusal = "کد منقضی شده است. دوباره درخواست دهید."
        elif challenge.attempts >= challenge.max_attempts:
            refusal = "تعداد تلاش‌ها تمام شده است. دوباره درخواست دهید."
        elif challenge.code_hash != _hash_code(code):
            OTPChallenge.objects.filter(pk=challenge.pk).update(attempts=F("attempts") + 1)
            refusal = "کد وارد شده نادرست است."
        elif not OTPChallenge.objects.filter(pk=challenge.pk, is_used=False).update(is_used=True):
            refusal = "کد معتبری یافت نشد. دوباره درخواست دهید."

    if refusal is not None:
        raise OTPValidationError(refusal)

    challenge.is_used = True
    return challenge


def request_login_otp(phone: str, *, request: HttpRequest | None = None) -> OTPRequestResult:
    phone = normalize_phone(phone)
    if not (phone.startswith("09") and len(phone) == 11):
        raise OTPValidationError("شماره موبایل معتبر نیست. مثال: ۰۹۱۲۳۴۵۶۷۸۹")

    _enforce_request_limits(phone=phone, purpose=OTPChallenge.Purpose.LOGIN, request=request)

    now = timezone.now()
    cooldown = settings.OTP_REQUEST_COOLDOWN_SECONDS
    code = "".join(secrets.choice("0123456789") for _ in range(settings.OTP_LENGTH))
    expires_at = now + timedelta(seconds=settings.OTP_EXPIRY_SECONDS)
    user_agent = ""
    if request is not None:
        user_agent = (request.META.get("HTTP_USER_AGENT") or "")[:255]

    # The challenge row is written even when no User owns this phone, so the
    # cooldown and hourly counters above behave identically for provisioned and
    # unprovisioned numbers. Skipping it here would turn the rate limiter itself
    # into a phone-enumeration oracle.
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

    # No SMS for a phone nobody can log in with: it would cost money and tell a
    # stranger that SANGA is interested in their number. The caller still gets a
    # normal-looking result; the refusal surfaces at verify time.
    is_provisioned = User.objects.filter(phone=phone).exists()
    if is_provisioned:
        body = f"کد ورود سنگا: {code}"
        try:
            get_sms_provider().send(SmsMessage(phone=phone, body=body))
        except Exception:
            logger.exception("Failed to send OTP SMS to %s", phone)
            raise OTPError("ارسال پیامک با مشکل روبه‌رو شد. دوباره تلاش کنید.") from None
    else:
        logger.info("OTP requested for unprovisioned phone; no SMS sent")

    dev_code = code if settings.DEBUG and settings.SMS_PROVIDER == "console" and is_provisioned else None
    return OTPRequestResult(
        phone=phone,
        expires_at=expires_at,
        cooldown_seconds=cooldown,
        dev_code=dev_code,
    )


# --- customer verification ----------------------------------------------------
#
# Deliberately a separate pair of functions from the staff login above, sharing
# only the hashing and rate-limiting primitives. A customer OTP:
#   * never creates a User,
#   * never calls ``login()``,
#   * uses its own ``Purpose``, so a code issued for one flow cannot be replayed
#     against the other.
#
# The provider abstraction is the same, so plugging in a production SMS gateway
# lights up both flows at once.


def request_customer_otp(phone: str, *, request: HttpRequest | None = None) -> OTPRequestResult:
    """Send a verification code to a retail customer."""
    phone = normalize_phone(phone)
    if not (phone.startswith("09") and len(phone) == 11):
        raise OTPValidationError("شماره موبایل معتبر نیست. مثال: ۰۹۱۲۳۴۵۶۷۸۹")

    _enforce_request_limits(phone=phone, purpose=OTPChallenge.Purpose.CUSTOMER, request=request)

    now = timezone.now()
    cooldown = settings.OTP_REQUEST_COOLDOWN_SECONDS
    code = "".join(secrets.choice("0123456789") for _ in range(settings.OTP_LENGTH))
    expires_at = now + timedelta(seconds=settings.OTP_EXPIRY_SECONDS)

    with transaction.atomic():
        OTPChallenge.objects.create(
            phone=phone,
            code_hash=_hash_code(code),
            purpose=OTPChallenge.Purpose.CUSTOMER,
            max_attempts=settings.OTP_MAX_ATTEMPTS,
            expires_at=expires_at,
            request_ip=_client_ip(request),
            user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:255] if request else "",
        )

    try:
        get_sms_provider().send(SmsMessage(phone=phone, body=f"کد تأیید سنگا: {code}"))
    except Exception:
        logger.exception("Failed to send customer OTP")
        raise OTPError("ارسال پیامک با مشکل روبه‌رو شد. دوباره تلاش کنید.") from None

    return OTPRequestResult(
        phone=phone,
        expires_at=expires_at,
        cooldown_seconds=cooldown,
        dev_code=code if settings.DEBUG and settings.SMS_PROVIDER == "console" else None,
    )


def verify_customer_otp(phone: str, code: str) -> bool:
    """Confirm a customer's phone. Returns ``True``; raises otherwise.

    Creates nothing and logs nobody in. The caller records the outcome on a
    ``CustomerLead``, which is not an account.
    """
    phone = normalize_phone(phone)
    code = normalize_digits((code or "").strip())
    if not code.isdigit():
        raise OTPValidationError("کد وارد شده معتبر نیست.")

    _claim_challenge(phone=phone, purpose=OTPChallenge.Purpose.CUSTOMER, code=code)
    return True


def verify_login_otp(phone: str, code: str, *, request: HttpRequest) -> User:
    phone = normalize_phone(phone)
    code = normalize_digits((code or "").strip())
    if not code.isdigit():
        raise OTPValidationError("کد وارد شده معتبر نیست.")

    # Claimed in its own transaction, outside the login one and before the
    # outcome is decided. Doing it inside would let the rollback on refusal hand
    # the code back, so a correct code for an unprovisioned phone could be
    # replayed the moment that phone is provisioned.
    _claim_challenge(phone=phone, purpose=OTPChallenge.Purpose.LOGIN, code=code)

    # Authentication never creates an account. Platform Users exist only because
    # a Platform Admin provisioned them.
    user = User.objects.filter(phone=phone).first()
    if user is None or not user.is_active:
        logger.warning("OTP verify refused for an unprovisioned or inactive phone")
        raise OTPValidationError(NOT_PROVISIONED_MESSAGE)

    with transaction.atomic():
        login(request, user)
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])
    return user
