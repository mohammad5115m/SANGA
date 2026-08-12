from __future__ import annotations

from decimal import Decimal

from django import forms

from apps.businesses.models import Business
from apps.contacts.models import Contact
from apps.inventory.models import InventoryLot

from .models import LedgerEntry

# Manual entry types offered in the UI (reversal is a separate, guarded action).
ENTRY_TYPE_CHOICES = [
    (LedgerEntry.Type.SALE.value, LedgerEntry.Type.SALE.label),
    (LedgerEntry.Type.PAYMENT_RECEIVED.value, LedgerEntry.Type.PAYMENT_RECEIVED.label),
    (LedgerEntry.Type.PURCHASE.value, LedgerEntry.Type.PURCHASE.label),
    (LedgerEntry.Type.PAYMENT_MADE.value, LedgerEntry.Type.PAYMENT_MADE.label),
    (LedgerEntry.Type.ADJUST_DEBIT.value, LedgerEntry.Type.ADJUST_DEBIT.label),
    (LedgerEntry.Type.ADJUST_CREDIT.value, LedgerEntry.Type.ADJUST_CREDIT.label),
]

ADJUSTMENT_TYPES = {LedgerEntry.Type.ADJUST_DEBIT.value, LedgerEntry.Type.ADJUST_CREDIT.value}

# The two sides of a trade, offered on the «ثبت معامله» screen.
TRADE_TYPE_CHOICES = [
    (LedgerEntry.Type.SALE.value, LedgerEntry.Type.SALE.label),
    (LedgerEntry.Type.PURCHASE.value, LedgerEntry.Type.PURCHASE.label),
]


class LedgerEntryForm(forms.Form):
    entry_type = forms.ChoiceField(
        label="نوع سند",
        choices=ENTRY_TYPE_CHOICES,
        widget=forms.Select(attrs={"class": "field-input"}),
    )
    amount = forms.DecimalField(
        label="مبلغ (ریال)",
        min_value=Decimal("0.01"),
        max_digits=14,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "field-input", "inputmode": "decimal", "step": "0.01"}),
    )
    occurred_on = forms.DateField(
        label="تاریخ",
        widget=forms.DateInput(attrs={"class": "field-input", "type": "date"}),
    )
    description = forms.CharField(
        label="شرح / دلیل",
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"class": "field-input", "placeholder": "مثلاً بابت بارنامه ۱۲۳"}),
    )
    reference = forms.CharField(
        label="مرجع/شماره سند (اختیاری)",
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={"class": "field-input"}),
    )
    related_lot = forms.ModelChoiceField(
        label="محموله مرتبط (اختیاری)",
        queryset=InventoryLot.objects.none(),
        required=False,
        empty_label="— بدون محموله —",
        widget=forms.Select(attrs={"class": "field-input"}),
    )

    def __init__(self, *args, business: Business, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["related_lot"].queryset = InventoryLot.objects.filter(
            business=business, deleted_at__isnull=True
        ).order_by("-updated_at")

    def clean(self):
        cleaned = super().clean()
        entry_type = cleaned.get("entry_type")
        description = (cleaned.get("description") or "").strip()
        if entry_type in ADJUSTMENT_TYPES and not description:
            self.add_error("description", "برای اصلاح دستی، ذکر دلیل الزامی است.")
        return cleaned


class TradeEntryForm(forms.Form):
    """Form for recording a trade (فروش / خرید) in the ledger.

    ``confirm`` is only required for the final submit, so the user can ask for a
    recalculated balance preview without being nagged for a confirmation they have
    not made yet.
    """

    entry_type = forms.ChoiceField(
        label="نوع معامله",
        choices=TRADE_TYPE_CHOICES,
        widget=forms.Select(attrs={"class": "field-input"}),
    )
    contact = forms.ModelChoiceField(
        label="طرف حساب (مخاطب شما)",
        queryset=Contact.objects.none(),
        empty_label="— انتخاب مخاطب —",
        widget=forms.Select(attrs={"class": "field-input"}),
    )
    amount = forms.DecimalField(
        label="مبلغ کل معامله (ریال)",
        min_value=Decimal("0.01"),
        max_digits=14,
        decimal_places=2,
        widget=forms.NumberInput(
            attrs={"class": "field-input", "inputmode": "decimal", "step": "0.01"}
        ),
    )
    occurred_on = forms.DateField(
        label="تاریخ سند",
        widget=forms.DateInput(attrs={"class": "field-input", "type": "date"}),
    )
    description = forms.CharField(
        label="شرح (اختیاری)",
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"class": "field-input"}),
    )
    reference = forms.CharField(
        label="مرجع/شماره سند (اختیاری)",
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={"class": "field-input"}),
    )
    related_lot = forms.ModelChoiceField(
        label="محموله مرتبط (اختیاری)",
        queryset=InventoryLot.objects.none(),
        required=False,
        empty_label="— بدون محموله —",
        widget=forms.Select(attrs={"class": "field-input"}),
    )
    confirm = forms.BooleanField(
        label="مبلغ و طرف حساب را بررسی کردم؛ ثبت این سند مالی را تأیید می‌کنم.",
        required=False,
        error_messages={"required": "برای ثبت سند، تأیید نهایی الزامی است."},
    )

    def __init__(self, *args, business: Business, require_confirm: bool = True, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["contact"].queryset = Contact.objects.filter(
            business=business, is_active=True
        ).order_by("display_name")
        # Only the acting business's own lots are selectable; the service checks
        # ownership again so a hand-crafted POST cannot attach a foreign lot.
        self.fields["related_lot"].queryset = InventoryLot.objects.filter(
            business=business, deleted_at__isnull=True
        ).order_by("-updated_at")
        self.fields["confirm"].required = require_confirm


class QuickContactForm(forms.Form):
    """Minimal contact creation from the trade screen, so the user never gets a
    silently auto-created contact but also does not have to leave the flow.
    """

    display_name = forms.CharField(
        label="نام / نام کسب‌وکار",
        max_length=200,
        widget=forms.TextInput(attrs={"class": "field-input"}),
    )
    phone = forms.CharField(
        label="موبایل/تلفن (اختیاری)",
        max_length=32,
        required=False,
        widget=forms.TextInput(attrs={"class": "field-input", "inputmode": "tel"}),
    )
    link_to_counterparty = forms.BooleanField(
        label="این مخاطب همان کسب‌وکار طرف معامله است (اتصال به همکار)",
        required=False,
    )
