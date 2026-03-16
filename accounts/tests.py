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

    def test_unique_code_per_sociedad(self):
        AccountClassification.objects.create(
            name="Revenue", code="4", sociedad="1100",
        )
        with self.assertRaises(IntegrityError):
            AccountClassification.objects.create(
                name="Duplicate", code="4", sociedad="1100",
            )

    def test_same_code_different_sociedad(self):
        AccountClassification.objects.create(
            name="Revenue 1000", code="4", sociedad="1000",
        )
        cls_1100 = AccountClassification.objects.create(
            name="Revenue 1100", code="4", sociedad="1100",
        )
        self.assertEqual(
            AccountClassification.objects.filter(code="4").count(), 2,
        )
        self.assertEqual(cls_1100.sociedad, "1100")

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

    def test_unique_code_per_sociedad(self):
        Account.objects.create(
            code="4101", name="Sales", classification=self.classification,
            sociedad="1100",
        )
        with self.assertRaises(IntegrityError):
            Account.objects.create(
                code="4101",
                name="Other Sales",
                classification=self.classification,
                sociedad="1100",
            )

    def test_same_code_different_sociedad(self):
        Account.objects.create(
            code="4101", name="Sales 1000",
            classification=self.classification, sociedad="1000",
        )
        Account.objects.create(
            code="4101", name="Sales 1100",
            classification=self.classification, sociedad="1100",
        )
        self.assertEqual(Account.objects.filter(code="4101").count(), 2)

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


# --------------------------------------------------------------------------
# PUC Schema tests
# --------------------------------------------------------------------------

class PUCSchemaTests(TestCase):
    """Tests for the puc_schemas module."""

    def test_get_schema_1000(self):
        from accounts.puc_schemas import get_puc_schema
        schema = get_puc_schema("1000")
        self.assertIsInstance(schema, dict)
        self.assertGreater(len(schema), 0)

    def test_get_schema_1100(self):
        from accounts.puc_schemas import get_puc_schema
        schema = get_puc_schema("1100")
        self.assertIsInstance(schema, dict)
        self.assertGreater(len(schema), 0)

    def test_invalid_sociedad_raises(self):
        from accounts.puc_schemas import get_puc_schema
        with self.assertRaises(ValueError):
            get_puc_schema("9999")

    def test_schema_entries_are_tuples(self):
        from accounts.puc_schemas import PUC_SCHEMA_1000, PUC_SCHEMA_1100
        for code, value in list(PUC_SCHEMA_1000.items())[:5]:
            self.assertIsInstance(value, tuple)
            self.assertEqual(len(value), 2)
        for code, value in list(PUC_SCHEMA_1100.items())[:5]:
            self.assertIsInstance(value, tuple)
            self.assertEqual(len(value), 2)

    def test_known_account_1000(self):
        from accounts.puc_schemas import PUC_SCHEMA_1000
        self.assertIn("5105030100", PUC_SCHEMA_1000)

    def test_known_account_1100(self):
        from accounts.puc_schemas import PUC_SCHEMA_1100
        self.assertIn("4120950100", PUC_SCHEMA_1100)
        cat, subcat = PUC_SCHEMA_1100["4120950100"]
        self.assertEqual(cat, "INGRESOS")
        self.assertEqual(subcat, "Nacionales")


# --------------------------------------------------------------------------
# Command tests
# --------------------------------------------------------------------------

class TaskCreacionCuentasCategoriaTests(TestCase):
    """Tests for the taskcreacioncuentascategoria management command."""

    def test_asignar_found_in_schema(self):
        from accounts.management.commands.taskcreacioncuentascategoria import Command
        schema = {"4120950100": ("INGRESOS", "Nacionales")}
        cat, subcat = Command.asignar_categoria_subcategoria("4120950100", schema)
        self.assertEqual(cat, "INGRESOS")
        self.assertEqual(subcat, "Nacionales")

    def test_asignar_not_found_returns_none(self):
        from accounts.management.commands.taskcreacioncuentascategoria import Command
        schema = {"4120950100": ("INGRESOS", "Nacionales")}
        cat, subcat = Command.asignar_categoria_subcategoria("9999999999", schema)
        self.assertIsNone(cat)
        self.assertIsNone(subcat)

    def test_asignar_strips_whitespace(self):
        from accounts.management.commands.taskcreacioncuentascategoria import Command
        schema = {"4120950100": ("INGRESOS", "Nacionales")}
        cat, subcat = Command.asignar_categoria_subcategoria(
            "  4120950100  ", schema,
        )
        self.assertEqual(cat, "INGRESOS")

    def test_sync_classifications_creates_hierarchy(self):
        from io import StringIO
        from django.core.management.color import no_style
        from accounts.management.commands.taskcreacioncuentascategoria import Command

        cmd = Command()
        cmd.stdout = StringIO()
        cmd.style = no_style()

        # Use a small subset of the PUC 1100 schema
        small_schema = {
            "4120950100": ("INGRESOS", "Nacionales"),
            "4120950200": ("INGRESOS", "Exterior"),
            "5105030100": ("GASTOS OPERACIONALES", "Administracion"),
        }
        cmd._sync_classifications("1100", small_schema)

        # Check categories were created
        cat_ingresos = AccountClassification.objects.filter(
            name="INGRESOS", sociedad="1100", level=0,
        )
        self.assertTrue(cat_ingresos.exists())

        # Check subcategories
        sub_nacionales = AccountClassification.objects.filter(
            name="Nacionales", sociedad="1100", level=1,
            parent=cat_ingresos.first(),
        )
        self.assertTrue(sub_nacionales.exists())

        sub_exterior = AccountClassification.objects.filter(
            name="Exterior", sociedad="1100", level=1,
            parent=cat_ingresos.first(),
        )
        self.assertTrue(sub_exterior.exists())

    def test_sync_classifications_idempotent(self):
        from io import StringIO
        from django.core.management.color import no_style
        from accounts.management.commands.taskcreacioncuentascategoria import Command

        cmd = Command()
        cmd.stdout = StringIO()
        cmd.style = no_style()

        schema = {"4120950100": ("INGRESOS", "Nacionales")}

        # Run twice
        cmd._sync_classifications("1100", schema)
        cmd._sync_classifications("1100", schema)

        # Only one category and one subcategory should exist
        self.assertEqual(
            AccountClassification.objects.filter(
                name="INGRESOS", sociedad="1100", level=0,
            ).count(),
            1,
        )
        self.assertEqual(
            AccountClassification.objects.filter(
                name="Nacionales", sociedad="1100", level=1,
            ).count(),
            1,
        )

    def test_separate_schemas_per_sociedad(self):
        from io import StringIO
        from django.core.management.color import no_style
        from accounts.management.commands.taskcreacioncuentascategoria import Command

        cmd = Command()
        cmd.stdout = StringIO()
        cmd.style = no_style()

        schema_1000 = {"5105030100": ("GASTOS DE ADMINISTRACIÓN", "Personal")}
        schema_1100 = {"5105030100": ("GASTOS OPERACIONALES", "Administracion")}

        cmd._sync_classifications("1000", schema_1000)
        cmd._sync_classifications("1100", schema_1100)

        # Both societies should have their own categories
        self.assertTrue(
            AccountClassification.objects.filter(
                name="GASTOS DE ADMINISTRACIÓN", sociedad="1000",
            ).exists()
        )
        self.assertTrue(
            AccountClassification.objects.filter(
                name="GASTOS OPERACIONALES", sociedad="1100",
            ).exists()
        )

    def test_sync_classifications_stores_cat_subcat(self):
        """Category (level 0) stores cat; subcategory (level 1) stores cat and subcat."""
        from io import StringIO
        from django.core.management.color import no_style
        from accounts.management.commands.taskcreacioncuentascategoria import Command

        cmd = Command()
        cmd.stdout = StringIO()
        cmd.style = no_style()

        small_schema = {
            "4120950100": ("INGRESOS", "Nacionales"),
            "4120950200": ("INGRESOS", "Exterior"),
        }
        cmd._sync_classifications("1100", small_schema)

        # Level-0 category should have cat set
        cat_obj = AccountClassification.objects.get(
            level=0, sociedad="1100", name="INGRESOS",
        )
        self.assertEqual(cat_obj.cat, "INGRESOS")
        self.assertEqual(cat_obj.subcat, "")

        # Level-1 subcategory should have both cat and subcat set
        sub_nac = AccountClassification.objects.get(
            level=1, sociedad="1100", name="Nacionales",
            parent=cat_obj,
        )
        self.assertEqual(sub_nac.cat, "INGRESOS")
        self.assertEqual(sub_nac.subcat, "Nacionales")

        sub_ext = AccountClassification.objects.get(
            level=1, sociedad="1100", name="Exterior",
            parent=cat_obj,
        )
        self.assertEqual(sub_ext.cat, "INGRESOS")
        self.assertEqual(sub_ext.subcat, "Exterior")

    def test_account_lookup_uses_cat_subcat(self):
        """New accounts are matched to classifications via cat/subcat fields."""
        from io import StringIO
        from unittest.mock import patch
        from django.core.management.color import no_style
        from accounts.management.commands.taskcreacioncuentascategoria import Command

        cmd = Command()
        cmd.stdout = StringIO()
        cmd.style = no_style()

        puc_schema = {
            "4120950100": ("INGRESOS", "Nacionales"),
            "5105030100": ("GASTOS OPERACIONALES", "Administracion"),
        }

        # Step 1 – sync classifications
        cmd._sync_classifications("1100", puc_schema)

        # Step 2 – mock SAP to return one known account
        fake_cuentas = [
            {
                "PLAN DE CUENTA": "YINC",
                "CTA. MAYOR": "4120950100",
                "DESCRIPCIÓN": "Ventas nacionales",
            },
        ]
        with patch.object(cmd, "_lista_cuentas_sap", return_value=fake_cuentas):
            cmd.handle(sociedad="1100")

        # The account should be linked to the correct subcategory
        acct = Account.objects.get(code="4120950100", sociedad="1100")
        self.assertEqual(acct.classification.cat, "INGRESOS")
        self.assertEqual(acct.classification.subcat, "Nacionales")
