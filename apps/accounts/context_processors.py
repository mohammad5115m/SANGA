from django.conf import settings


def authentication_context(request):
    return {
        "password_login_enabled": settings.AUTH_LOGIN_METHOD == "password",
        "customer_otp_enabled": settings.CUSTOMER_OTP_ENABLED,
        "public_registration_enabled": settings.PUBLIC_REGISTRATION_ENABLED,
    }
