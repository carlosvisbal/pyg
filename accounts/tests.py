from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase

from .models import (
    Account,
    AccountClassification,
    AccountEditHistory,
    Adjustment,
    AdjustmentDetail,
    AdjustmentHistory,
    Period,
    SAPRecord,
)

User = get_user_model()


class AccountClassificationTests(TestCase):
    """Tests for the AccountClassification model."""

    def test_create_root_classification(self):
        classification = AccountClassification.objects.create(
            name="Revenue",
            code="4",
            level=0,
            sign=1,
        )
        self.assertEqual(str(classification), "4 - Revenue")
        self.assertIsNone(classification.parent)
        self.assertTrue(classification.is_active)

    def test_create_child_classification(self):
        parent = AccountClassification.objects.create(
            name="Revenue", code="4", level=0, sign=1,
        )
        child = AccountClassification.objects.create(
            name="Sales Revenue", code="41", parent=parent, level=1, sign=1,
        )
        self.assertEqual(child.parent, parent)
        self.assertIn(child, parent.children.all())

    def test_unique_code(self):
        AccountClassification.objects.create(name="Revenue", code="4")
        with self.assertRaises(IntegrityError):
            AccountClassification.objects.create(name="Duplicate", code="4")

    def test_ordering(self):
        AccountClassification.objects.create(name="Expenses", code="5")
        AccountClassification.objects.create(name="Revenue", code="4")
        codes = list(
            AccountClassification.objects.values_list("code", flat=True)
        )
        self.assertEqual(codes, ["4", "5"])


class AccountTests(TestCase):
    """Tests for the Account model."""

    def setUp(self):
        self.classification = AccountClassification.objects.create(
            name="Revenue", code="4", sign=1,
        )

    def test_create_account(self):
        account = Account.objects.create(
            code="4101",
            name="Sales",
            classification=self.classification,
        )
        self.assertEqual(str(account), "4101 - Sales")
        self.assertTrue(account.is_active)

    def test_unique_code(self):
        Account.objects.create(
            code="4101", name="Sales", classification=self.classification,
        )
        with self.assertRaises(IntegrityError):
            Account.objects.create(
                code="4101",
                name="Other Sales",
                classification=self.classification,
            )

    def test_classification_protect_on_delete(self):
        Account.objects.create(
            code="4101", name="Sales", classification=self.classification,
        )
        with self.assertRaises(Exception):
            self.classification.delete()


class PeriodTests(TestCase):
    """Tests for the Period model."""

    def test_create_period(self):
        period = Period.objects.create(year=2026, month=1)
        self.assertEqual(str(period), "2026-01")
        self.assertFalse(period.is_closed)

    def test_unique_year_month(self):
        Period.objects.create(year=2026, month=1)
        with self.assertRaises(IntegrityError):
            Period.objects.create(year=2026, month=1)


class SAPRecordTests(TestCase):
    """Tests for the SAPRecord model."""

    def setUp(self):
        self.classification = AccountClassification.objects.create(
            name="Revenue", code="4", sign=1,
        )
        self.account = Account.objects.create(
            code="4101", name="Sales", classification=self.classification,
        )
        self.period = Period.objects.create(year=2026, month=3)

    def test_create_sap_record(self):
        record = SAPRecord.objects.create(
            account=self.account,
            period=self.period,
            amount=Decimal("1000000.50"),
        )
        self.assertIn("4101", str(record))
        self.assertIn("2026-03", str(record))

    def test_unique_account_period(self):
        SAPRecord.objects.create(
            account=self.account, period=self.period, amount=Decimal("100"),
        )
        with self.assertRaises(IntegrityError):
            SAPRecord.objects.create(
                account=self.account,
                period=self.period,
                amount=Decimal("200"),
            )


class AdjustmentTests(TestCase):
    """Tests for the Adjustment and AdjustmentDetail models."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="auditor", password="test1234",
        )
        cls = AccountClassification.objects.create(
            name="Revenue", code="4", sign=1,
        )
        self.account_a = Account.objects.create(
            code="4101", name="Sales A", classification=cls,
        )
        self.account_b = Account.objects.create(
            code="4102", name="Sales B", classification=cls,
        )
        self.period = Period.objects.create(year=2026, month=3)

    def test_create_balanced_adjustment(self):
        adj = Adjustment.objects.create(
            period=self.period,
            description="Reclassify sales",
            created_by=self.user,
        )
        AdjustmentDetail.objects.create(
            adjustment=adj, account=self.account_a, amount=Decimal("-500"),
        )
        AdjustmentDetail.objects.create(
            adjustment=adj, account=self.account_b, amount=Decimal("500"),
        )
        total = sum(d.amount for d in adj.details.all())
        self.assertEqual(total, Decimal("0"))

    def test_adjustment_str(self):
        adj = Adjustment.objects.create(
            period=self.period,
            description="Reclassify sales",
            created_by=self.user,
        )
        self.assertIn("2026-03", str(adj))

    def test_cascade_delete_details(self):
        adj = Adjustment.objects.create(
            period=self.period,
            description="Test",
            created_by=self.user,
        )
        AdjustmentDetail.objects.create(
            adjustment=adj, account=self.account_a, amount=Decimal("100"),
        )
        adj.delete()
        self.assertEqual(AdjustmentDetail.objects.count(), 0)


class AccountEditHistoryTests(TestCase):
    """Tests for the AccountEditHistory audit trail."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="admin", password="test1234",
        )
        self.classification = AccountClassification.objects.create(
            name="Revenue", code="4", sign=1,
        )
        self.account = Account.objects.create(
            code="4101", name="Sales", classification=self.classification,
        )

    def test_create_history_entry(self):
        entry = AccountEditHistory.objects.create(
            account=self.account,
            action=AccountEditHistory.ActionType.CREATED,
            changes={"name": {"old": None, "new": "Sales"}},
            performed_by=self.user,
        )
        self.assertEqual(entry.action, "CREATED")
        self.assertIn("4101", str(entry))

    def test_update_history_entry(self):
        AccountEditHistory.objects.create(
            account=self.account,
            action=AccountEditHistory.ActionType.UPDATED,
            changes={"name": {"old": "Sales", "new": "Net Sales"}},
            performed_by=self.user,
        )
        self.assertEqual(self.account.edit_history.count(), 1)


class AdjustmentHistoryTests(TestCase):
    """Tests for the AdjustmentHistory audit trail."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="admin", password="test1234",
        )
        cls = AccountClassification.objects.create(
            name="Revenue", code="4", sign=1,
        )
        self.account = Account.objects.create(
            code="4101", name="Sales", classification=cls,
        )
        self.period = Period.objects.create(year=2026, month=3)
        self.adjustment = Adjustment.objects.create(
            period=self.period,
            description="Test adjustment",
            created_by=self.user,
        )

    def test_create_history_entry(self):
        entry = AdjustmentHistory.objects.create(
            adjustment=self.adjustment,
            action=AdjustmentHistory.ActionType.CREATED,
            changes={},
            performed_by=self.user,
        )
        self.assertEqual(entry.action, "CREATED")

    def test_reversal_history(self):
        AdjustmentHistory.objects.create(
            adjustment=self.adjustment,
            action=AdjustmentHistory.ActionType.REVERSED,
            changes={"reason": "Error in original entry"},
            performed_by=self.user,
            note="Reversed due to incorrect account.",
        )
        self.assertEqual(self.adjustment.history.count(), 1)
        self.assertEqual(
            self.adjustment.history.first().action, "REVERSED",
        )
