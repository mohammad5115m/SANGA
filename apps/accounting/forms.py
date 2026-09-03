from __future__ import annotations

from django import forms
from django.utils import timezone

from apps.core.widgets import JalaliDateWidget

from .models import LedgerEntry
from .services import MANUAL_ENTRY_TYPES

_TEXT = {"class": "field-input"}


class ManualEntryForm(forms.Form):
    """The four manual entries a user may post.

    Deliberately not a cheque register. A cheque number or a maturity date goes
    in «مرجع» or «شرح» if the user wants to remember it; SANGA does not track
    cheque status, bank reconciliation or instalment schedules, because money
    moves outside the application and pretending otherwise produces a second set
    of books that disagrees with the real one.
    """

    entry_type = forms.ChoiceField(
        label="نوع سند",
        choices=[(value, LedgerEntry.Type(value).label) for value in MANUAL_ENTRY_TYPES],
        widget=forms.Select(attrs=_TEXT),
    )
    amount = forms.DecimalField(
        label="مبلغ (ریال)",
        min_value=0,
        decimal_places=2,
        max_digits=14,
        widget=forms.NumberInput(attrs={**_TEXT, "inputmode": "numeric"}),
    )
    occurred_on = forms.DateField(
        label="تاریخ",
        initial=timezone.localdate,
        widget=JalaliDateWidget(attrs={**_TEXT, "type": "date"}, format="%Y-%m-%d"),
    )
    description = forms.CharField(
        label="شرح",
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={**_TEXT, "placeholder": "مثلاً واریز به حساب، چک شماره ۱۲۳..."}),
    )
    reference = forms.CharField(
        label="مرجع / شماره سند",
        required=False,
        max_length=100,
        widget=forms.TextInput(attrs={**_TEXT, "dir": "ltr"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["occurred_on"].input_formats = ["%Y-%m-%d"]

    def clean(self):
        cleaned = super().clean()
        adjustments = {LedgerEntry.Type.ADJUST_DEBIT, LedgerEntry.Type.ADJUST_CREDIT}
        if cleaned.get("entry_type") in adjustments and not (cleaned.get("description") or "").strip():
            # An unexplained correction is indistinguishable from a mistake when
            # somebody reads the books six months later.
            self.add_error("description", "برای اصلاح دستی، ذکر دلیل الزامی است.")
        return cleaned
