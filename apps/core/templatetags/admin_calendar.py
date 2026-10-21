from datetime import date, datetime

from django import template

from apps.core.calendar import format_jalali

register = template.Library()


@register.simple_tag
def admin_calendar_value(field):
    """Format raw readonly dates; leave existing escaped admin rendering intact."""
    name = field.field.get("name")
    value = getattr(field.form.instance, name, None) if isinstance(name, str) else None
    if isinstance(value, date):
        return format_jalali(value, with_time=isinstance(value, datetime))
    return field.contents()
