from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from .forms import OTPVerifyForm, PhoneLoginForm
from .services import OTPError, request_login_otp, verify_login_otp

logger = logging.getLogger(__name__)


@require_http_methods(["GET", "POST"])
def login_view(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("businesses:dashboard")

    form = PhoneLoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
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
                messages.info(request, f"کد توسعه (فقط DEBUG): {result.dev_code}")
            return redirect("accounts:verify")

    return render(request, "accounts/login.html", {"form": form})


@require_http_methods(["GET", "POST"])
def verify_view(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("businesses:dashboard")

    phone = request.session.get("otp_phone", "")
    if not phone:
        messages.error(request, "ابتدا شماره موبایل خود را وارد کنید.")
        return redirect("accounts:login")

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
            messages.success(request, "با موفقیت وارد شدید.")
            return redirect("businesses:post_login")

    return render(request, "accounts/verify.html", {"form": form, "phone": phone})


@login_required
@require_POST
def logout_view(request: HttpRequest) -> HttpResponse:
    """POST only.

    Logging out changes state, and accepting GET meant any page anywhere could
    end a SANGA session — an `<img src="…/auth/logout/">` in an email, a link in
    a forum post. Harmless-looking on its own, and a reliable way to make the
    application unusable for someone while they are trying to work.
    """
    logout(request)
    messages.info(request, "از حساب خارج شدید.")
    return redirect("accounts:login")


@login_required
@require_http_methods(["GET", "POST"])
def profile_view(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        full_name = (request.POST.get("full_name") or "").strip()[:150]
        request.user.full_name = full_name
        request.user.save(update_fields=["full_name"])
        messages.success(request, "پروفایل به‌روزرسانی شد.")
        return redirect("accounts:profile")
    return render(request, "accounts/profile.html")
