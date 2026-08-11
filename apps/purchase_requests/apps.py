from django.apps import AppConfig


class PurchaseRequestsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.purchase_requests"
    label = "purchase_requests"
    verbose_name = "Purchase Requests"
