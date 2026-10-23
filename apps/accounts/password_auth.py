"""Password authentication and reviewed owner registration, without SMS."""

from django.conf import settings
from django.contrib.auth import authenticate, login
from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import validate_password
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.debug import sensitive_variables

from apps.core.persian import normalize_phone

from .models import OTPRequestThrottle, RegistrationRequest, User
from .services import OTPError, _client_ip, _hit_throttle

LOGIN_REFUSED = "شماره موبایل یا رمز عبور نادرست است، یا حساب هنوز تأیید نشده است."


@transaction.atomic
def claim_attempt(*, phone, request, purpose, phone_limit=None):
    """Separate budgets for passwords, registration and customer inquiries."""
    now = timezone.now()
    _hit_throttle(
        scope=OTPRequestThrottle.Scope.PHONE,
        key=f"{purpose}:{phone}",
        limit=phone_limit or settings.AUTH_MAX_ATTEMPTS_PER_HOUR,
        cooldown_seconds=0,
        now=now,
    )
    address = _client_ip(request)
    if address:
        _hit_throttle(
            scope=OTPRequestThrottle.Scope.ADDRESS,
            key=f"{purpose}:{address}",
            limit=settings.AUTH_MAX_ATTEMPTS_PER_IP_PER_HOUR,
            cooldown_seconds=0,
            now=now,
        )


@sensitive_variables("password")
def password_login(*, phone, password, request):
    if settings.AUTH_LOGIN_METHOD != "password":
        raise OTPError("ورود با رمز عبور در این مرحله فعال نیست.")
    phone = normalize_phone(phone)
    claim_attempt(phone=phone, request=request, purpose="password")
    user = authenticate(request=request, username=phone, password=password)
    if user is None:
        raise OTPError(LOGIN_REFUSED)
    login(request, user)
    request.session.pop("otp_phone", None)
    request.session.pop("otp_dev_code", None)
    return user


@sensitive_variables("password", "encoded", "details")
def register_owner(*, phone, password, full_name, business_name, city, request):
    if not settings.PUBLIC_REGISTRATION_ENABLED:
        raise OTPError("ثبت‌نام عمومی فعال نیست.")
    phone = normalize_phone(phone)
    if not (phone.startswith("09") and len(phone) == 11):
        raise OTPError("شماره موبایل معتبر نیست.")
    values = [(full_name.strip(), 150), (business_name.strip(), 200), (city.strip(), 100)]
    if any(not 2 <= len(value) <= maximum for value, maximum in values) or not 8 <= len(password) <= 128:
        raise OTPError("اطلاعات ثبت‌نام معتبر نیست.")
    claim_attempt(phone=phone, request=request, purpose="registration", phone_limit=5)
    validate_password(password, user=User(phone=phone, full_name=full_name))
    encoded = make_password(password)
    # Never replace credentials, revive an account, or overwrite a waiting
    # request just because somebody can type its phone number.
    if User.objects.filter(phone=phone).exists():
        return
    details = {
        "full_name": full_name.strip(),
        "business_name": business_name.strip(),
        "city": city.strip(),
        "password_hash": encoded,
    }
    with transaction.atomic():
        current, _ = RegistrationRequest.objects.get_or_create(phone=phone, defaults=details)
        current = RegistrationRequest.objects.select_for_update().get(pk=current.pk)
        if current.status == RegistrationRequest.Status.REJECTED:
            for name, value in details.items():
                setattr(current, name, value)
            current.status = RegistrationRequest.Status.PENDING
            current.reviewed_by = None
            current.reviewed_at = None
            current.save(update_fields=[*details, "status", "reviewed_by", "reviewed_at"])


@transaction.atomic
def review_registration(*, registration, actor, approve):
    from apps.businesses.services import create_business_for_owner

    if not (actor.is_active and actor.is_superuser):
        raise OTPError("فقط مدیر پلتفرم اجازه بررسی ثبت‌نام را دارد.")
    current = RegistrationRequest.objects.select_for_update().get(pk=registration.pk)
    if current.status != RegistrationRequest.Status.PENDING:
        return current
    if approve:
        if User.objects.filter(phone=current.phone).exists():
            raise OTPError("این شماره اکنون حساب دارد؛ درخواست را رد و حساب موجود را بررسی کنید.")
        try:
            with transaction.atomic():
                user = User.objects.create_user(phone=current.phone, full_name=current.full_name)
                user.password = current.password_hash
                user.save(update_fields=["password"])
                slug = f"{slugify(current.business_name, allow_unicode=True)[:180]}-{current.pk.hex}"
                current.business = create_business_for_owner(
                    owner=user,
                    name=current.business_name,
                    city=current.city,
                    verified=True,
                    slug=slug,
                )
        except IntegrityError as exc:
            raise OTPError("حساب این شماره همزمان تغییر کرده است؛ درخواست را دوباره بررسی کنید.") from exc
        current.status = RegistrationRequest.Status.APPROVED
    else:
        current.status = RegistrationRequest.Status.REJECTED
    current.password_hash = ""
    current.reviewed_by = actor
    current.reviewed_at = timezone.now()
    current.save(update_fields=["status", "business", "password_hash", "reviewed_by", "reviewed_at"])
    return current
