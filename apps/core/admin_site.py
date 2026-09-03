"""Persian presentation for all registered admins, preserving their permission hooks."""

from __future__ import annotations

from datetime import datetime, time, timedelta

from django import forms
from django.contrib import admin
from django.contrib.admin.apps import AdminConfig
from django.core.exceptions import FieldDoesNotExist
from django.db import models
from django.utils import timezone, translation

from .calendar import format_jalali, jalali_parts
from .widgets import DateRangeForm, JalaliDateTimeWidget, JalaliDateWidget


class PersianAdminMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.inlines = [
            type("Persian" + inline.__name__, (PersianAdminMixin, inline), {})
            if not issubclass(inline, PersianAdminMixin) else inline
            for inline in getattr(self, "inlines", ())
        ]
        # Celery's scheduler uses Django's Gregorian drill-down. The shared
        # range filter provides exact Jalali dates as well as month/year shortcuts.
        if getattr(self, "date_hierarchy", None):
            field = self.date_hierarchy
            if field not in self.list_filter:
                self.list_filter = [*self.list_filter, field]
            self.date_hierarchy = None

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if isinstance(db_field, models.DateTimeField):
            kwargs.update(form_class=forms.DateTimeField, widget=JalaliDateTimeWidget)
        elif isinstance(db_field, models.DateField):
            kwargs.update(form_class=forms.DateField, widget=JalaliDateWidget)
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    def get_list_display(self, request):
        columns = []
        for column in super().get_list_display(request):
            try:
                field = self.model._meta.get_field(column) if isinstance(column, str) else None
            except FieldDoesNotExist:
                field = None
            if isinstance(field, models.DateField):
                def display(obj, name=column, with_time=isinstance(field, models.DateTimeField)):
                    return format_jalali(getattr(obj, name), with_time=with_time)
                display.__name__ = "jalali_" + column
                columns.append(admin.display(description=field.verbose_name, ordering=column)(display))
            else:
                columns.append(column)
        return columns


class SangaAdminSite(admin.AdminSite):
    site_header = "مدیریت سنگا"
    site_title = "سنگا"
    index_title = "مدیریت کسب‌وکارها و اطلاعات"

    def register(self, model_or_iterable, admin_class=None, **options):
        admin_class = admin_class or admin.ModelAdmin
        if not issubclass(admin_class, PersianAdminMixin):
            admin_class = type("Persian" + admin_class.__name__, (PersianAdminMixin, admin_class), {})
        return super().register(model_or_iterable, admin_class, **options)

    def admin_view(self, view, cacheable=False):
        wrapped = super().admin_view(view, cacheable=cacheable)
        def persian_view(request, *args, **kwargs):
            with translation.override("fa"):
                request.LANGUAGE_CODE = "fa"
                response = wrapped(request, *args, **kwargs)
                # TemplateResponse would otherwise render after the locale override exits.
                if hasattr(response, "render"):
                    response.render()
                return response
        return persian_view

    def login(self, request, extra_context=None):
        with translation.override("fa"):
            request.LANGUAGE_CODE = "fa"
            response = super().login(request, extra_context=extra_context)
            if hasattr(response, "render"):
                response.render()
            return response


class SangaAdminConfig(AdminConfig):
    default_site = "apps.core.admin_site.SangaAdminSite"

    def ready(self):
        super().ready()
        admin.FieldListFilter.register(
            lambda field: isinstance(field, models.DateField), PersianDateFilter, take_priority=True,
        )


class PersianDateFilter(admin.DateFieldListFilter):
    """Month/year shortcuts use Jalali boundaries, with canonical query parameters."""

    template = "admin/jalali_filter.html"

    def expected_parameters(self):
        original = super().expected_parameters()
        return original + [self.lookup_kwarg_since + "_jalali", self.lookup_kwarg_until + "_jalali"]

    def __init__(self, field, request, params, model, model_admin, field_path):
        lower, upper = field_path + "__gte", field_path + "__lt"
        self.date_form = DateRangeForm(request.GET, start_name=lower, end_name=upper)
        has_companion = lower + "_jalali" in request.GET or upper + "_jalali" in request.GET
        self.invalid = has_companion and not self.date_form.is_valid()
        if has_companion:
            for name in (lower, upper):
                params.pop(name + "_jalali", None)
                params.pop(name, None)
            if not self.invalid:
                start = self.date_form.cleaned_data.get(lower)
                end = self.date_form.cleaned_data.get(upper)
                if start:
                    value = timezone.make_aware(datetime.combine(start, time.min)) if isinstance(
                        field, models.DateTimeField,
                    ) else start
                    params[lower] = [value.isoformat()]
                if end:
                    value = end + timedelta(days=1)
                    if isinstance(field, models.DateTimeField):
                        value = timezone.make_aware(datetime.combine(value, time.min))
                    params[upper] = [value.isoformat()]
        else:
            initial = {}
            for name in (lower, upper):
                raw = request.GET.get(name, "")[:10]
                if raw:
                    try:
                        parsed = datetime.strptime(raw, "%Y-%m-%d").date()
                        initial[name] = parsed - timedelta(days=1) if name == upper else parsed
                    except ValueError:
                        pass
            self.date_form = DateRangeForm(start_name=lower, end_name=upper, initial=initial)
        super().__init__(field, request, params, model, model_admin, field_path)
        self.query_params = [
            (key, value) for key, values in request.GET.lists()
            if key not in {*self.expected_parameters(), "p", "e"}
            for value in values
        ]
        today = timezone.localdate()
        current = jalali_parts(today)
        month_start = current.replace(day=1).togregorian()
        year_start = current.replace(month=1, day=1).togregorian()
        next_month = (
            current.replace(year=current.year + 1, month=1, day=1)
            if current.month == 12 else current.replace(month=current.month + 1, day=1)
        ).togregorian()
        next_year = current.replace(year=current.year + 1, month=1, day=1).togregorian()
        def bounds(start, end):
            if isinstance(field, models.DateTimeField):
                start = timezone.make_aware(datetime.combine(start, time.min))
                end = timezone.make_aware(datetime.combine(end, time.min))
            return {self.lookup_kwarg_since: str(start), self.lookup_kwarg_until: str(end)}
        self.links = [
            ("همه تاریخ‌ها", {}),
            ("امروز", bounds(today, today + timedelta(days=1))),
            ("هفت روز گذشته", bounds(today - timedelta(days=6), today + timedelta(days=1))),
            ("این ماه شمسی", bounds(month_start, next_month)),
            ("این سال شمسی", bounds(year_start, next_year)),
        ]
        if field.null:
            self.links += [
                ("بدون تاریخ", {self.lookup_kwarg_isnull: "True"}),
                ("دارای تاریخ", {self.lookup_kwarg_isnull: "False"}),
            ]

    def queryset(self, request, queryset):
        if self.invalid:
            return queryset.none()
        return super().queryset(request, queryset)
