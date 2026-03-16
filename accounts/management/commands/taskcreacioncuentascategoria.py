"""
Management command: taskcreacioncuentascategoria

Synchronises the SAP account master for a given *sociedad*.

**Phase 1** – Create category and subcategory ``AccountClassification``
records from the PUC schema (``puc_schemas.py``).  Categories must exist
*before* accounts so that new accounts arriving from SAP can be
classified immediately.

**Phase 2** – Fetch the list of accounts from the SAP ledger via ODBC
and create any missing ``Account`` records, assigning each one to its
corresponding subcategory via the ``cat`` / ``subcat`` lookup.
"""

from django.core.management.base import BaseCommand

from accounts.models import Account, AccountClassification
from accounts.puc_schemas import get_puc_schema

MAX_CODE_LENGTH = AccountClassification._meta.get_field("code").max_length


class Command(BaseCommand):
    help = (
        "Sincroniza el maestro de cuentas del libro SAP para una sociedad "
        "(1000 o 1100).  Fase 1: crea categorías y subcategorías desde el "
        "esquema PUC.  Fase 2: carga cuentas desde SAP y las clasifica."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "sociedad",
            type=str,
            help="Código de sociedad (1000 o 1100).",
            default="1100",
            nargs="?",
        )

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------
    def handle(self, *args, **kwargs):
        sociedad = kwargs["sociedad"]
        puc_schema = get_puc_schema(sociedad)

        # ── Phase 1: Create category & subcategory classifications ────
        self.stdout.write(
            f"Fase 1 – Creando categorías y subcategorías para "
            f"sociedad {sociedad}…"
        )
        self._sync_classifications(sociedad, puc_schema)

        # ── Phase 2: Fetch accounts from SAP and classify them ────────
        self.stdout.write(
            f"Fase 2 – Cargando cuentas desde SAP para "
            f"sociedad {sociedad}…"
        )
        cuentas = self._lista_cuentas_sap(sociedad)
        nuevas, sin_asignar = self._create_accounts(
            sociedad, cuentas, puc_schema,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Sincronización completada para sociedad {sociedad}: "
                f"{nuevas} cuentas nuevas, {sin_asignar} sin asignación."
            )
        )

    # ------------------------------------------------------------------
    # Phase 1 – Classification sync
    # ------------------------------------------------------------------
    def _sync_classifications(self, sociedad, puc_schema):
        """Create category (level 0) and subcategory (level 1) records.

        Builds a ``{category: {subcategory, …}}`` tree from the schema
        and ensures every node exists in the database.
        """
        tree: dict[str, set[str]] = {}
        for _code, (cat, subcat) in puc_schema.items():
            tree.setdefault(cat, set()).add(subcat)

        for nombre_cat, subcats in sorted(tree.items()):
            cat_code = nombre_cat[:MAX_CODE_LENGTH]
            categoria, cat_created = AccountClassification.objects.get_or_create(
                code=cat_code,
                sociedad=sociedad,
                defaults={
                    "name": nombre_cat,
                    "level": 0,
                    "cat": nombre_cat,
                },
            )
            if cat_created:
                self.stdout.write(
                    self.style.SUCCESS(f"Categoría creada: {nombre_cat}")
                )

            for nombre_subcat in sorted(subcats):
                sub_code = f"{cat_code}:{nombre_subcat}"[:MAX_CODE_LENGTH]
                _subcat, sub_created = AccountClassification.objects.get_or_create(
                    code=sub_code,
                    sociedad=sociedad,
                    defaults={
                        "name": nombre_subcat,
                        "parent": categoria,
                        "level": 1,
                        "cat": nombre_cat,
                        "subcat": nombre_subcat,
                    },
                )
                if sub_created:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  Subcategoría creada: {nombre_subcat} "
                            f"(categoría: {nombre_cat})"
                        )
                    )

    # ------------------------------------------------------------------
    # Phase 2 – Account creation from SAP data
    # ------------------------------------------------------------------
    def _create_accounts(self, sociedad, cuentas, puc_schema):
        """Create ``Account`` records from the SAP account list.

        Each account is matched to its subcategory classification using
        the ``cat`` / ``subcat`` fields, so the category tree **must**
        already exist in the database before calling this method.

        Returns ``(created_count, unassigned_count)``.
        """
        nuevas = 0
        sin_asignar = 0

        for cuenta in cuentas:
            codigo = str(cuenta["CTA. MAYOR"]).strip()
            descripcion = cuenta["DESCRIPCIÓN"]

            nombre_cat, nombre_subcat = self.asignar_categoria_subcategoria(
                codigo, puc_schema,
            )

            if nombre_cat is None:
                self.stdout.write(
                    self.style.WARNING(
                        f"Sin esquema para la cuenta {codigo} "
                        f"en sociedad {sociedad}"
                    )
                )
                sin_asignar += 1
                continue

            # Retrieve the subcategory classification
            subcategoria = AccountClassification.objects.filter(
                cat=nombre_cat,
                subcat=nombre_subcat,
                sociedad=sociedad,
            ).first()

            if subcategoria is None:
                self.stdout.write(
                    self.style.WARNING(
                        f"Subcategoría '{nombre_subcat}' no encontrada para "
                        f"categoría '{nombre_cat}' en sociedad {sociedad}"
                    )
                )
                sin_asignar += 1
                continue

            _account, created = Account.objects.get_or_create(
                code=codigo,
                sociedad=sociedad,
                defaults={
                    "name": descripcion,
                    "classification": subcategoria,
                    "description": descripcion,
                },
            )

            if created:
                nuevas += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Cuenta creada: {codigo} - {descripcion}"
                    )
                )

        return nuevas, sin_asignar

    # ------------------------------------------------------------------
    # Category / subcategory lookup (data-driven)
    # ------------------------------------------------------------------
    @staticmethod
    def asignar_categoria_subcategoria(
        codigo: str,
        puc_schema: dict[str, tuple[str, str]],
    ) -> tuple[str | None, str | None]:
        """Look up an account code in the PUC schema and return
        ``(category_name, subcategory_name)``.

        Returns ``(None, None)`` when the code is not found.
        """
        codigo = codigo.strip()
        result = puc_schema.get(codigo)
        if result is not None:
            return result
        return (None, None)

    # ------------------------------------------------------------------
    # SAP data access
    # ------------------------------------------------------------------
    def _lista_cuentas_sap(self, sociedad):
        """Fetch account list from SAP via ODBC.

        Kept as a separate method so it can be easily mocked in tests.
        """
        try:
            import pyodbc  # noqa: WPS433
        except ImportError:
            self.stdout.write(
                self.style.ERROR(
                    "pyodbc no está instalado. "
                    "No se pueden obtener cuentas de SAP."
                )
            )
            return []

        sociedad = str(sociedad)
        conn_str = (
            "DRIVER=ODBC Driver 17 for SQL Server;"
            " SERVER=;"
            " DATABASE=;"
            " UID=;"
            " PWD="
        )
        with pyodbc.connect(conn_str) as conn:
            cursor = conn.cursor()
            query = (
                "SELECT SKA1.KTOPL AS [PLAN DE CUENTA], "
                "       SKA1.SAKNR AS [CTA. MAYOR], "
                "       TXT50       AS [DESCRIPCIÓN] "
                "FROM tgp.SKA1 "
                "INNER JOIN tgp.SKAT "
                "  ON SKA1.KTOPL = SKAT.KTOPL "
                " AND SKA1.SAKNR = SKAT.SAKNR "
                "WHERE SKA1.KTOPL = ("
                "  SELECT TOP 1 KTOPL FROM tgp.T001 WHERE BUKRS = ?"
                ")"
            )
            cursor.execute(query, (sociedad,))
            column_names = [desc[0] for desc in cursor.description]
            return [dict(zip(column_names, row)) for row in cursor]
