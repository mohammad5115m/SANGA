from __future__ import annotations

from datetime import timedelta

from django import forms
from django.utils import timezone

from .crm import CATEGORY_CHOICES, CUSTOMER_STATUS_CHOICES, PRIORITY_CHOICES


class CustomerProfileForm(forms.Form):
    category = forms.ChoiceField(
        label="دسته‌بندی مشتری",
        choices=CATEGORY_CHOICES,
        widget=forms.Select(attrs={"class": "field-input"}),
    )
    crm_status = forms.ChoiceField(
        label="وضعیت ارتباط",
        choices=CUSTOMER_STATUS_CHOICES,
        widget=forms.Select(attrs={"class": "field-input"}),
    )
    tags = forms.CharField(
        label="برچسب‌ها",
        max_length=300,
        required=False,
        help_text="برچسب‌ها را با ویرگول جدا کنید.",
        widget=forms.TextInput(
            attrs={"class": "field-input", "placeholder": "مثلاً پروژه ویلایی، خرید تکراری"}
        ),
    )
    current_needs = forms.CharField(
        label="نیاز فعلی",
        max_length=1000,
        required=False,
        widget=forms.Textarea(
            attrs={"class": "field-input", "rows": 3, "placeholder": "کاربرد، متراژ، زمان و ترجیح‌های مشتری"}
        ),
    )


class CustomerNoteForm(forms.Form):
    text = forms.CharField(
        label="یادداشت جدید",
        max_length=1000,
        widget=forms.Textarea(
            attrs={
                "class": "field-input",
                "rows": 3,
                "placeholder": "نتیجه گفت‌وگو، ترجیح مشتری یا نکته بعدی…",
            }
        ),
    )


class FollowUpForm(forms.Form):
    title = forms.CharField(
        label="موضوع پیگیری",
        max_length=160,
        widget=forms.TextInput(
            attrs={"class": "field-input", "placeholder": "مثلاً اعلام قیمت نهایی"}
        ),
    )
    scheduled_for = forms.DateTimeField(
        label="تاریخ و ساعت",
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(
            attrs={"class": "field-input", "type": "datetime-local"},
            format="%Y-%m-%dT%H:%M",
        ),
    )
    reminder_minutes = forms.TypedChoiceField(
        label="زمان یادآوری",
        coerce=int,
        choices=(
            (0, "در زمان پیگیری"),
            (15, "۱۵ دقیقه قبل"),
            (60, "۱ ساعت قبل"),
            (1440, "۱ روز قبل"),
        ),
        initial=60,
        widget=forms.Select(attrs={"class": "field-input"}),
    )
    priority = forms.ChoiceField(
        label="اولویت",
        choices=PRIORITY_CHOICES,
        initial="normal",
        widget=forms.Select(attrs={"class": "field-input"}),
    )
    related_context = forms.CharField(
        label="درخواست یا محصول مرتبط",
        max_length=255,
        required=False,
        widget=forms.TextInput(
            attrs={"class": "field-input", "placeholder": "مثلاً درخواست نمای ساختمان"}
        ),
    )
    note = forms.CharField(
        label="توضیح (اختیاری)",
        max_length=1000,
        required=False,
        widget=forms.Textarea(attrs={"class": "field-input", "rows": 3}),
    )

    def __init__(self, *args, **kwargs):
        initial = dict(kwargs.pop("initial", {}) or {})
        initial.setdefault("scheduled_for", timezone.localtime() + timedelta(days=1))
        kwargs["initial"] = initial
        super().__init__(*args, **kwargs)


class CompleteFollowUpForm(forms.Form):
    note = forms.CharField(
        label="نتیجه پیگیری (اختیاری)",
        max_length=1000,
        required=False,
        widget=forms.Textarea(
            attrs={"class": "field-input", "rows": 2, "placeholder": "نتیجه تماس یا اقدام بعدی…"}
        ),
    )


class RescheduleFollowUpForm(forms.Form):
    scheduled_for = forms.DateTimeField(
        label="زمان جدید",
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(
            attrs={"class": "field-input", "type": "datetime-local"},
            format="%Y-%m-%dT%H:%M",
        ),
    )
