from __future__ import annotations

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_POST

from .forms import OTPVerifyForm, PasswordLoginForm, PhoneLoginForm, ProfileForm, RegistrationForm
from .password_auth import password_login, register_owner
from .services import OTPError, request_login_otp, verify_login_otp

logger = logging.getLogger(__name__)


@sensitive_post_parameters("password")
@never_cache
@require_http_methods(["GET", "POST"])
def login_view(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        if request.user.is_active and request.user.is_superuser:
            return redirect("admin:index")
        return redirect("businesses:dashboard")

    data = request.POST if request.method == "POST" else None
    form = PasswordLoginForm(data) if settings.AUTH_LOGIN_METHOD == "password" else PhoneLoginForm(data)
    if request.method == "POST" and form.is_valid():
        if settings.AUTH_LOGIN_METHOD == "password":
            try:
                user = password_login(
                    phone=form.cleaned_data["phone"], password=form.cleaned_data["password"], request=request,
                )
            except OTPError as exc:
                form.add_error(None, exc.message)
            else:
                return redirect("admin:index" if user.is_superuser else "businesses:post_login")
            return render(request, "accounts/login.html", {"form": form})
        try:
            result = request_login_otp(form.cleaned_data["phone"], request=request)
        except OTPError as exc:
            form.add_error(None, exc.message)
        except Exception:
            logger.exception("Unexpected OTP request failure")
            form.add_error(None, "خطای غیرمنتظره رخ داد. دوباره تلاش کنید.")
        else:
            request.session["otp_phone"] = result.phone
            if result.dev_code:
                request.session["otp_dev_code"] = result.dev_code
            else:
                request.session.pop("otp_dev_code", None)
            return redirect("accounts:verify")

    return render(request, "accounts/login.html", {"form": form})


@require_http_methods(["GET", "POST"])
def verify_view(request: HttpRequest) -> HttpResponse:
    if settings.AUTH_LOGIN_METHOD != "otp":
        request.session.pop("otp_phone", None)
        request.session.pop("otp_dev_code", None)
        return redirect("accounts:login")
    if request.user.is_authenticated:
        if request.user.is_active and request.user.is_superuser:
            return redirect("admin:index")
        return redirect("businesses:dashboard")

    phone = request.session.get("otp_phone", "")
    if not phone:
        request.session.pop("otp_dev_code", None)
        messages.error(request, "ابتدا شماره موبایل خود را وارد کنید.")
        return redirect("accounts:login")

    # Console is the explicit no-gateway/test mode. Gate again at render time so
    # a stale session cannot expose a code after switching to a real provider.
    show_dev_code = (settings.SMS_PROVIDER or "").strip().lower() == "console"
    dev_code = request.session.get("otp_dev_code") if show_dev_code else None

    form = OTPVerifyForm(request.POST or None, initial={"phone": phone})
    if request.method == "POST" and form.is_valid():
        try:
            # The session phone is authoritative; the hidden form field is only
            # a UX convenience and could be tampered with.
            verify_login_otp(phone, form.cleaned_data["code"], request=request)
        except OTPError as exc:
            form.add_error(None, exc.message)
        except Exception:
            logger.exception("Unexpected OTP verify failure")
            form.add_error(None, "خطای غیرمنتظره رخ داد. دوباره تلاش کنید.")
        else:
            request.session.pop("otp_phone", None)
            request.session.pop("otp_dev_code", None)
            messages.success(request, "با موفقیت وارد شدید.")
            if request.user.is_active and request.user.is_superuser:
                if request.headers.get("HX-Request") == "true":
                    response = HttpResponse(status=204)
                    response["HX-Redirect"] = reverse("admin:index")
                    return response
                return redirect("admin:index")
            return redirect("businesses:post_login")

    return render(
        request,
        "accounts/verify.html",
        {"form": form, "phone": phone, "dev_code": dev_code},
    )


@login_required
@require_POST
def logout_view(request: HttpRequest) -> HttpResponse:
    """POST only.

    Logging out changes state, and accepting GET meant any page anywhere could
    end a Sang Esf session — an `<img src="…/auth/logout/">` in an email, a link in
    a forum post. Harmless-looking on its own, and a reliable way to make the
    application unusable for someone while they are trying to work.
    """
    logout(request)
    messages.info(request, "از حساب خارج شدید.")
    return redirect("accounts:login")


@login_required
@require_http_methods(["GET", "POST"])
def profile_view(request: HttpRequest) -> HttpResponse:
    form = ProfileForm(request.POST if request.method == "POST" else None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save(commit=False).save(update_fields=["full_name"])
        messages.success(request, "پروفایل به‌روزرسانی شد.")
        return redirect("accounts:profile")
    return render(request, "accounts/profile.html", {"form": form})


@sensitive_post_parameters("password1", "password2")
@never_cache
@require_http_methods(["GET", "POST"])
def register_view(request):
    if not settings.PUBLIC_REGISTRATION_ENABLED:
        return redirect("accounts:login")
    if request.user.is_authenticated:
        return redirect("businesses:post_login")
    form = RegistrationForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            register_owner(
                phone=form.cleaned_data["phone"], password=form.cleaned_data["password1"],
                full_name=form.cleaned_data["full_name"], business_name=form.cleaned_data["business_name"],
                city=form.cleaned_data["city"], request=request,
            )
        except OTPError as exc:
            form.add_error(None, exc.message)
        except ValidationError as exc:
            form.add_error("password1", exc)
        else:
            return redirect("accounts:registration_submitted")
    return render(request, "accounts/register.html", {"form": form})


@require_http_methods(["GET"])
def registration_submitted(request):
    return render(request, "accounts/registration_submitted.html")


@login_required
@sensitive_post_parameters("old_password", "new_password1", "new_password2")
@never_cache
@require_http_methods(["GET", "POST"])
def password_change(request):
    form = PasswordChangeForm(request.user, request.POST if request.method == "POST" else None)
    for field in form.fields.values():
        field.widget.attrs.update({"class": "field-input", "dir": "ltr"})
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        messages.success(request, "رمز عبور تغییر کرد.")
        return redirect("accounts:profile")
    return render(request, "accounts/password_change.html", {"form": form})
