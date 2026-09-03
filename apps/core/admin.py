"""Shared admin behaviour for records that are history.

Django Admin is a technical tool with full table access, and the domain rules
that make SANGA's financial records trustworthy live in services. That leaves a
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


class HistoricalRecordAdmin(NoDeleteAdmin):
    """A record that becomes read-only once it is no longer a draft.

    Subclasses answer :meth:`is_final` for their own lifecycle. While a record is
    still a draft the normal admin fields stay editable; once it is issued,
    finalized or cancelled, every field is read-only and the only way to change
    the commercial position is the workflow the domain provides.
    """

    def is_final(self, obj) -> bool:
        return obj is not None

    def get_readonly_fields(self, request, obj=None):
        base = tuple(super().get_readonly_fields(request, obj))
        if obj is None or not self.is_final(obj):
            return base
        return tuple(dict.fromkeys((*base, *self._all_field_names(obj))))

    def has_change_permission(self, request, obj=None) -> bool:
        # Kept True so the record can still be *opened* and read. Every field is
        # read-only above, so nothing can be written; refusing outright would
        # hide history from the operators who most need to look at it.
        return super().has_change_permission(request, obj)

    def _all_field_names(self, obj) -> tuple[str, ...]:
        return tuple(
            field.name
            for field in obj._meta.get_fields()
            if getattr(field, "editable", False) and not field.auto_created
        )
