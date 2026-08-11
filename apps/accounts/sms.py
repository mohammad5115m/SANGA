from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

from django.conf import settings

logger = logging.getLogger("apps.accounts.sms")


@dataclass(frozen=True)
class SmsMessage:
    phone: str
    body: str


class SmsProvider(ABC):
    @abstractmethod
    def send(self, message: SmsMessage) -> None:
        raise NotImplementedError


class ConsoleSmsProvider(SmsProvider):
    """Development-safe provider: logs OTP instead of sending SMS."""

    def send(self, message: SmsMessage) -> None:
        logger.info("SMS to %s :: %s", message.phone, message.body)


class NullSmsProvider(SmsProvider):
    def send(self, message: SmsMessage) -> None:
        return None


def get_sms_provider() -> SmsProvider:
    provider = getattr(settings, "SMS_PROVIDER", "console").lower()
    if provider == "console":
        return ConsoleSmsProvider()
    if provider == "null":
        return NullSmsProvider()
    # Future: kavenegar/ghasedak/etc. adapters registered here.
    logger.warning("Unknown SMS_PROVIDER=%s; falling back to console", provider)
    return ConsoleSmsProvider()
