"""Shared admin behaviour for records that are history.

Django Admin is a technical tool with full table access, and the domain rules
that make Sang Esf's financial records trustworthy live in services. That leaves a
gap: a superuser could edit a finalized Trade's amount or delete an issued
invoice, and none of the invariants the services enforce would notice.

The rule these mixins express is the same one the ledger already follows —
commercial history is corrected by cancellation and reversal, never by editing
or deleting the original. Admin is not the place to make an exception, because
an exception made there is invisible to everything else.
"""

from __future__ import annotations

from django.contrib import admin


class NoDeleteAdmin(admin.ModelAdmin):
    """Records that must never be removed from the database through admin."""

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


class ReadOnlyAdmin(NoDeleteAdmin):
    """Inspect service-owned records without exposing a second write workflow."""

    change_form_template = "admin/readonly_change_form.html"

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        # View permission keeps GET/history/export available. Reject POST before
        # validation or save hooks can mutate records or raise an unhandled error.
        return False

    def get_readonly_fields(self, request, obj=None):
        base = tuple(super().get_readonly_fields(request, obj))
        return tuple(dict.fromkeys((*base, *(field.name for field in self.model._meta.fields))))


class ReadOnlyTabularInline(admin.TabularInline):
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)
