from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import login
from django.db import IntegrityError, transaction
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


TOO_MANY_REQUESTS = "تعداد درخواست‌های شما بیش از حد مجاز است. بعداً تلاش کنید."
COOLDOWN_MESSAGE = "لطفاً کمی صبر کنید و دوباره تلاش کنید."


def _configured_login_allowlist() -> set[str]:
    """Return valid, normalized phones explicitly approved for OTP provisioning."""
    phones: set[str] = set()
    for raw_phone in getattr(settings, "SANGA_LOGIN_PHONE_ALLOWLIST", []):
        phone = normalize_phone(str(raw_phone))
        if phone.startswith("09") and len(phone) == 11:
            phones.add(phone)
    return phones


def _ensure_allowlisted_login_account(phone: str) -> None:
    """Provision/repair the complete approved login account, including its Business."""
    if phone not in _configured_login_allowlist():
        return

    # Import lazily: businesses depends on the accounts model, so importing it at
    # module load time would create a circular application dependency.
    from apps.businesses.models import Business, BusinessMembership
    from apps.businesses.services import complete_onboarding, create_business_for_owner

    profile = getattr(settings, "SANGA_LOGIN_ACCOUNT_DEFAULTS", {}).get(phone, {})
    with transaction.atomic():
        user, created = User.objects.get_or_create(
            phone=phone,
            defaults={
                "is_active": True,
                "full_name": str(profile.get("full_name", "")).strip(),
            },
        )
        # Serialize account repair on the User row. Without this lock, two first
        # OTP requests could both observe no membership and create two businesses.
        user = User.objects.select_for_update().get(pk=user.pk)
        user_updates: list[str] = []
        if not user.is_active:
            user.is_active = True
            user_updates.append("is_active")
        configured_name = str(profile.get("full_name", "")).strip()
        if configured_name and not user.full_name:
            user.full_name = configured_name
            user_updates.append("full_name")
        if user_updates:
            user.save(update_fields=user_updates)

        membership = (
            BusinessMembership.objects.select_for_update()
            .select_related("business")
            .filter(user=user, role=BusinessMembership.Role.OWNER)
            .first()
        )
        if membership is None:
            business = create_business_for_owner(
                owner=user,
                name=str(profile.get("business_name") or f"کسب‌وکار {phone[-4:]}"),
                city=str(profile.get("city", "")),
                province=str(profile.get("province", "")),
                phone=phone,
            )
            complete_onboarding(business)
        else:
            business = membership.business
            membership_updates: list[str] = []
            if membership.status != BusinessMembership.Status.ACTIVE:
                membership.status = BusinessMembership.Status.ACTIVE
                membership_updates.append("status")
            if membership_updates:
                membership.save(update_fields=membership_updates)

            business_updates: list[str] = []
            if business.status != Business.Status.ACTIVE:
                business.status = Business.Status.ACTIVE
                business_updates.append("status")
            if business.verification_status != Business.VerificationStatus.VERIFIED:
                business.verification_status = Business.VerificationStatus.VERIFIED
                business_updates.append("verification_status")
            if business.plan != Business.Plan.SELLER:
                business.plan = Business.Plan.SELLER
                business_updates.append("plan")
            if business.active_until is not None:
                business.active_until = None
                business_updates.append("active_until")
            if business_updates:
                business_updates.append("updated_at")
                business.save(update_fields=business_updates)
            if not business.is_onboarded:
                complete_onboarding(business)

    logger.info(
        "Provisioned or repaired an explicitly allowlisted OTP account%s",
        " (new user)" if created else "",
    )


def _client_ip(request: HttpRequest | None) -> str | None:
    """The address to rate-limit on, which is not simply whatever the client says.

    ``X-Forwarded-For`` is a header, and a header is written by whoever sent the
    request. Reading the leftmost entry — the previous behaviour — meant any
    caller could defeat the per-address cap by inventing a new value per request,
    and could equally attribute their traffic to somebody else's address and get
    that person throttled.

    So SANGA counts hops from the *right*, where the trusted infrastructure
    appended them, and only as many as ``SANGA_TRUSTED_PROXY_COUNT`` says it
    actually has. With the default of 0 the header is ignored entirely, which is
    the correct behaviour for a deployment reached directly and the safe default
    for one that is misconfigured.

    The reverse proxy must **overwrite** the header rather than append to it; see
    docs/deployment.md. This function is the second line of defence, not the
    first.
    """
    if request is None:
        return None

    remote = request.META.get("REMOTE_ADDR")
    trusted = int(getattr(settings, "SANGA_TRUSTED_PROXY_COUNT", 0) or 0)
    if trusted <= 0:
        return remote

    forwarded = [part.strip() for part in (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")]
    forwarded = [part for part in forwarded if part]
    if not forwarded:
        return remote
    # The rightmost entry was added by the proxy nearest to us. Stepping left
    # once per trusted hop lands on the first value we did not add ourselves; a
    # client that supplied extras only pads the part we never look at.
    index = len(forwarded) - trusted
    return forwarded[index] if 0 <= index < len(forwarded) else forwarded[0]


def _hit_throttle(*, scope: str, key: str, limit: int, cooldown_seconds: int, now) -> None:
    """Claim one request against a limit, under a row lock.

    Must be called inside the same transaction that writes the challenge, so the
    check and the thing it is limiting cannot be separated by another request.

    ``get_or_create`` then ``select_for_update`` rather than the other way round:
    there is nothing to lock until the row exists, and the unique constraint is
    what makes two concurrent first-requests resolve to one row instead of two
    counters that never see each other.
    """
    from .models import OTPRequestThrottle

    window_start = now - timedelta(hours=1)

    try:
        OTPRequestThrottle.objects.get_or_create(
            scope=scope,
            key=key,
            defaults={"window_started_at": now, "count": 0, "last_request_at": window_start},
        )
    except IntegrityError:
        # Lost the race to create it. The winner's row is the one we want.
        pass

    row = OTPRequestThrottle.objects.select_for_update().get(scope=scope, key=key)

    if row.window_started_at < window_start:
        # Window elapsed: start a fresh one rather than accumulating forever.
        row.window_started_at = now
        row.count = 0

    if cooldown_seconds and (now - row.last_request_at).total_seconds() < cooldown_seconds:
        raise OTPRateLimitError(COOLDOWN_MESSAGE)

    if row.count >= limit:
        raise OTPRateLimitError(TOO_MANY_REQUESTS)

    row.count += 1
    row.last_request_at = now
    row.save(update_fields=["window_started_at", "count", "last_request_at"])


def _enforce_request_limits(*, phone: str, purpose: str, request: HttpRequest | None) -> None:
    """Cooldown, per-phone hourly cap, and a per-address cap across both purposes.

    Every limit is claimed under a lock on its own row, inside the caller's
    transaction. Previously this counted rows and returned, and the challenge was
    inserted afterwards in a separate transaction — so two requests arriving
    together both counted the same rows, both concluded they were under the
    limit, and both inserted. Every one of these limits could be walked past by
    sending requests in parallel rather than in sequence.

    The phone row is always locked before the address row. Two requests for one
    phone from behind one proxy take the two locks in the same order and one
    simply waits; taking them in different orders is how a deadlock happens.

    Limits are claimed for unprovisioned phones too, so they behave identically
    whether or not the number belongs to an account. Skipping them would turn the
    rate limiter itself into a phone-enumeration oracle.
    """
    from .models import OTPRequestThrottle

    now = timezone.now()

    _hit_throttle(
        scope=OTPRequestThrottle.Scope.PHONE,
        key=f"{purpose}:{phone}",
        limit=settings.OTP_MAX_REQUESTS_PER_HOUR,
        cooldown_seconds=settings.OTP_REQUEST_COOLDOWN_SECONDS,
        now=now,
    )

    ip = _client_ip(request)
    if ip:
        try:
            _hit_throttle(
                scope=OTPRequestThrottle.Scope.ADDRESS,
                key=ip,
                limit=settings.OTP_MAX_REQUESTS_PER_IP_PER_HOUR,
                # No cooldown per address: several people behind one office
                # connection are not abuse, and the hourly cap already bounds it.
                cooldown_seconds=0,
                now=now,
            )
        except OTPRateLimitError:
            logger.warning("OTP request rate limit hit for one source address")
            raise


def _send_code(*, phone: str, body: str) -> None:
    """Hand the message to the gateway, converting vendor failures into one error.

    Called after the challenge transaction has committed, never inside it. A
    gateway call is slow and can fail, and holding the throttle row locks across
    it would serialize every OTP request in the system behind the slowest SMS.
    Sending from inside would also be wrong in the other direction: a rollback
    after the send would deliver a code no row exists for.
    """
    try:
        get_sms_provider().send(SmsMessage(phone=phone, body=body))
    except Exception:
        # No body, no code, no credentials — the message is the one thing here
        # that must never reach a log.
        logger.exception("Failed to send an OTP message")
        raise OTPError("ارسال پیامک با مشکل روبه‌رو شد. دوباره تلاش کنید.") from None


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

    _ensure_allowlisted_login_account(phone)

    now = timezone.now()
    cooldown = settings.OTP_REQUEST_COOLDOWN_SECONDS
    code = "".join(secrets.choice("0123456789") for _ in range(settings.OTP_LENGTH))
    expires_at = now + timedelta(seconds=settings.OTP_EXPIRY_SECONDS)
    user_agent = ""
    if request is not None:
        user_agent = (request.META.get("HTTP_USER_AGENT") or "")[:255]

    # One transaction covers claiming the rate limits and writing the challenge.
    # Splitting them is what let two simultaneous requests both pass the limits
    # before either had written anything for the other to count.
    #
    # The challenge row is written even when no User owns this phone, so the
    # counters behave identically for provisioned and unprovisioned numbers.
    # Skipping it would turn the rate limiter itself into an enumeration oracle.
    with transaction.atomic():
        _enforce_request_limits(phone=phone, purpose=OTPChallenge.Purpose.LOGIN, request=request)
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
        _send_code(phone=phone, body=f"کد ورود سنگا: {code}")
    else:
        logger.info("OTP requested for unprovisioned phone; no SMS sent")

    # Console mode is the explicit no-gateway/test mode. It must remain usable
    # even in a staging deployment with DEBUG=False; production settings already
    # refuse this provider unless the operator deliberately allows undelivered
    # OTPs. Return a code for every phone so this does not reveal whether an
    # account exists. Verification still refuses unprovisioned users.
    dev_code = code if (settings.SMS_PROVIDER or "").strip().lower() == "console" else None
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

    now = timezone.now()
    cooldown = settings.OTP_REQUEST_COOLDOWN_SECONDS
    code = "".join(secrets.choice("0123456789") for _ in range(settings.OTP_LENGTH))
    expires_at = now + timedelta(seconds=settings.OTP_EXPIRY_SECONDS)

    with transaction.atomic():
        _enforce_request_limits(phone=phone, purpose=OTPChallenge.Purpose.CUSTOMER, request=request)
        OTPChallenge.objects.create(
            phone=phone,
            code_hash=_hash_code(code),
            purpose=OTPChallenge.Purpose.CUSTOMER,
            max_attempts=settings.OTP_MAX_ATTEMPTS,
            expires_at=expires_at,
            request_ip=_client_ip(request),
            user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:255] if request else "",
        )

    _send_code(phone=phone, body=f"کد تأیید سنگا: {code}")

    return OTPRequestResult(
        phone=phone,
        expires_at=expires_at,
        cooldown_seconds=cooldown,
        dev_code=(
            code if (settings.SMS_PROVIDER or "").strip().lower() == "console" else None
        ),
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
