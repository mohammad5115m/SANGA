from __future__ import annotations

import decimal
import uuid

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("businesses", "0007_replace_request_capabilities"),
        ("inventory", "0012_product_inventory_simplification"),
        ("trading", "0004_backfill_trade_items"),
    ]

    operations = [
        migrations.CreateModel(
            name="TradeProposal",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("submission_id", models.UUIDField(blank=True, editable=False, null=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("draft", "پیش‌نویس"),
                            ("pending", "در انتظار تأیید"),
                            ("confirmed", "تأیید و نهایی شد"),
                            ("rejected", "رد شد"),
                            ("cancelled", "لغو شد"),
                        ],
                        default="draft",
                        max_length=20,
                    ),
                ),
                (
                    "total_amount",
                    models.DecimalField(
                        decimal_places=2,
                        default=decimal.Decimal("0"),
                        max_digits=16,
                        validators=[django.core.validators.MinValueValidator(decimal.Decimal("0"))],
                        verbose_name="مبلغ کل",
                    ),
                ),
                ("currency", models.CharField(default="IRR", max_length=3)),
                ("note", models.TextField(blank=True, verbose_name="توضیح")),
                ("submitted_at", models.DateTimeField(blank=True, null=True)),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "buyer_business",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="purchase_proposals",
                        to="businesses.business",
                    ),
                ),
                (
                    "confirmed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="trade_proposals_confirmed",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="trade_proposals_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "initiated_by_business",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="initiated_trade_proposals",
                        to="businesses.business",
                    ),
                ),
                (
                    "seller_business",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sale_proposals",
                        to="businesses.business",
                    ),
                ),
                (
                    "trade",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="source_proposal",
                        to="trading.trade",
                    ),
                ),
            ],
            options={"verbose_name": "توافق معامله", "verbose_name_plural": "توافق‌های معامله", "ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="TradeProposalItem",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("product_name", models.CharField(max_length=200, verbose_name="نام محصول")),
                ("stone_type", models.CharField(blank=True, max_length=100, verbose_name="نوع سنگ")),
                ("grade", models.CharField(blank=True, max_length=50, verbose_name="سورت")),
                (
                    "quantity",
                    models.DecimalField(
                        decimal_places=3,
                        max_digits=12,
                        validators=[django.core.validators.MinValueValidator(decimal.Decimal("0.001"))],
                        verbose_name="متراژ",
                    ),
                ),
                ("unit", models.CharField(default="متر مربع", max_length=20, verbose_name="واحد")),
                (
                    "unit_price",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=14,
                        validators=[django.core.validators.MinValueValidator(decimal.Decimal("0"))],
                        verbose_name="قیمت واحد",
                    ),
                ),
                (
                    "line_total",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=16,
                        validators=[django.core.validators.MinValueValidator(decimal.Decimal("0"))],
                        verbose_name="جمع",
                    ),
                ),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                (
                    "item",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="trade_proposal_items",
                        to="inventory.inventorylot",
                    ),
                ),
                (
                    "proposal",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="items",
                        to="trading.tradeproposal",
                    ),
                ),
            ],
            options={
                "verbose_name": "ردیف توافق معامله",
                "verbose_name_plural": "ردیف‌های توافق معامله",
                "ordering": ["sort_order", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="tradeproposal",
            constraint=models.CheckConstraint(
                condition=~models.Q(("seller_business", models.F("buyer_business"))),
                name="trade_proposal_not_self",
            ),
        ),
        migrations.AddConstraint(
            model_name="tradeproposal",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("initiated_by_business", models.F("seller_business")))
                    | models.Q(("initiated_by_business", models.F("buyer_business")))
                ),
                name="trade_proposal_initiator_is_party",
            ),
        ),
        migrations.AddConstraint(
            model_name="tradeproposal",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("status", "confirmed"), ("trade__isnull", False))
                    | (~models.Q(("status", "confirmed")) & models.Q(("trade__isnull", True)))
                ),
                name="trade_proposal_trade_when_confirmed",
            ),
        ),
        migrations.AddConstraint(
            model_name="tradeproposal",
            constraint=models.UniqueConstraint(
                condition=models.Q(("submission_id__isnull", False)),
                fields=("initiated_by_business", "submission_id"),
                name="uniq_trade_proposal_submission",
            ),
        ),
        migrations.AddIndex(
            model_name="tradeproposal",
            index=models.Index(fields=["seller_business", "status", "-created_at"], name="trading_tra_seller__d22b35_idx"),
        ),
        migrations.AddIndex(
            model_name="tradeproposal",
            index=models.Index(fields=["buyer_business", "status", "-created_at"], name="trading_tra_buyer_b_ea2e5c_idx"),
        ),
    ]
