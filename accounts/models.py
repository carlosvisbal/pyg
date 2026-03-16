from django.conf import settings
from django.db import models
from django.utils import timezone

SOCIEDAD_CHOICES = [
    ("1000", "Sociedad 1000"),
    ("1100", "Sociedad 1100"),
]


class AccountClassification(models.Model):
    """Hierarchical classification for P&L accounts (e.g. Revenue > Sales)."""

    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50)
    sociedad = models.CharField(
        max_length=4,
        choices=SOCIEDAD_CHOICES,
        default="1100",
        help_text="Company code (sociedad) this classification belongs to.",
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
    )
    level = models.PositiveSmallIntegerField(
        default=0,
        help_text="Depth in the classification hierarchy (0 = root).",
    )
    sign = models.SmallIntegerField(
        default=1,
        help_text="1 for credit-normal (revenue), -1 for debit-normal (expense).",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]
        unique_together = [("code", "sociedad")]
        verbose_name = "Account Classification"
        verbose_name_plural = "Account Classifications"

    def __str__(self):
        return f"{self.code} - {self.name}"


class Account(models.Model):
    """Individual P&L account linked to a classification."""

    code = models.CharField(max_length=50)
    name = models.CharField(max_length=255)
    classification = models.ForeignKey(
        AccountClassification,
        on_delete=models.PROTECT,
        related_name="accounts",
    )
    sociedad = models.CharField(
        max_length=4,
        choices=SOCIEDAD_CHOICES,
        default="1100",
        help_text="Company code (sociedad) this account belongs to.",
    )
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]
        unique_together = [("code", "sociedad")]
        verbose_name = "Account"
        verbose_name_plural = "Accounts"

    def __str__(self):
        return f"{self.code} - {self.name}"


class Period(models.Model):
    """Fiscal period used to group SAP records and adjustments."""

    year = models.PositiveIntegerField()
    month = models.PositiveSmallIntegerField()
    is_closed = models.BooleanField(default=False)
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("year", "month")]
        ordering = ["-year", "-month"]
        verbose_name = "Period"
        verbose_name_plural = "Periods"

    def __str__(self):
        return f"{self.year}-{self.month:02d}"


class SAPRecord(models.Model):
    """Snapshot of account balances imported from SAP for a given period."""

    account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="sap_records",
    )
    period = models.ForeignKey(
        Period,
        on_delete=models.PROTECT,
        related_name="sap_records",
    )
    amount = models.DecimalField(max_digits=20, decimal_places=2)
    imported_at = models.DateTimeField(default=timezone.now)
    raw_data = models.JSONField(
        blank=True,
        default=dict,
        help_text="Original payload from SAP for audit purposes.",
    )

    class Meta:
        unique_together = [("account", "period")]
        ordering = ["-period__year", "-period__month", "account__code"]
        verbose_name = "SAP Record"
        verbose_name_plural = "SAP Records"

    def __str__(self):
        return f"{self.account.code} | {self.period} | {self.amount}"


class Adjustment(models.Model):
    """
    An adjustment moves an amount from one account to another within a period.

    The sum of all adjustments in a period must be zero (balanced).
    """

    period = models.ForeignKey(
        Period,
        on_delete=models.PROTECT,
        related_name="adjustments",
    )
    description = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="adjustments_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Adjustment"
        verbose_name_plural = "Adjustments"

    def __str__(self):
        return f"Adj #{self.pk} – {self.period} – {self.description[:50]}"


class AdjustmentDetail(models.Model):
    """
    Individual line of an adjustment (debit or credit entry).

    For every Adjustment the sum of all AdjustmentDetail.amount values must be 0.
    """

    adjustment = models.ForeignKey(
        Adjustment,
        on_delete=models.CASCADE,
        related_name="details",
    )
    account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="adjustment_details",
    )
    amount = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        help_text="Positive = credit, Negative = debit.",
    )

    class Meta:
        verbose_name = "Adjustment Detail"
        verbose_name_plural = "Adjustment Details"

    def __str__(self):
        return f"{self.account.code} -> {self.amount}"


class AccountEditHistory(models.Model):
    """
    Audit-trail entry recorded every time an Account is created, updated or
    deactivated.  Acts as a timeline for each account.
    """

    class ActionType(models.TextChoices):
        CREATED = "CREATED", "Created"
        UPDATED = "UPDATED", "Updated"
        DEACTIVATED = "DEACTIVATED", "Deactivated"
        REACTIVATED = "REACTIVATED", "Reactivated"

    account = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name="edit_history",
    )
    action = models.CharField(max_length=20, choices=ActionType.choices)
    changes = models.JSONField(
        default=dict,
        help_text="Snapshot of changed fields: {field: {old, new}}.",
    )
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    performed_at = models.DateTimeField(default=timezone.now)
    note = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-performed_at"]
        verbose_name = "Account Edit History"
        verbose_name_plural = "Account Edit Histories"

    def __str__(self):
        return f"{self.account.code} – {self.action} – {self.performed_at}"


class AdjustmentHistory(models.Model):
    """
    Audit-trail entry for adjustments – records every modification, approval
    or reversal of an Adjustment.
    """

    class ActionType(models.TextChoices):
        CREATED = "CREATED", "Created"
        MODIFIED = "MODIFIED", "Modified"
        REVERSED = "REVERSED", "Reversed"
        APPROVED = "APPROVED", "Approved"

    adjustment = models.ForeignKey(
        Adjustment,
        on_delete=models.CASCADE,
        related_name="history",
    )
    action = models.CharField(max_length=20, choices=ActionType.choices)
    changes = models.JSONField(
        default=dict,
        help_text="Snapshot of changed fields: {field: {old, new}}.",
    )
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    performed_at = models.DateTimeField(default=timezone.now)
    note = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-performed_at"]
        verbose_name = "Adjustment History"
        verbose_name_plural = "Adjustment Histories"

    def __str__(self):
        return f"Adj #{self.adjustment_id} – {self.action} – {self.performed_at}"
