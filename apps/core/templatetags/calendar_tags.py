from django import template

from apps.core.calendar import format_jalali, persian_digits
from apps.core.widgets import JalaliDateTimeWidget, JalaliDateWidget

register = template.Library()


@register.filter
def jdate(value):
    return format_jalali(value)


@register.filter
def jdatetime(value):
    return format_jalali(value, with_time=True)


register.filter("fa_digits", persian_digits)


@register.simple_tag
def jalali_input(name, value="", *, with_time=False, id=None, required=False):
    widget = JalaliDateTimeWidget() if with_time else JalaliDateWidget()
    return widget.render(name, value, attrs={"id": id or "id_" + name, "required": required})


@register.simple_tag
def persian_font_data():
    from apps.core.fonts import invoice_font_data
    return invoice_font_data()
