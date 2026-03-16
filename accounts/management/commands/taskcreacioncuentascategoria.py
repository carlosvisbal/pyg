"""
Management command: taskcreacioncuentascategoria

Synchronises the SAP account master for a given *sociedad*.

**Phase 1** – Read category and subcategory ``AccountClassification``
records from the database.  These records must have been previously
created by ``load_categories_from_excel``.  A warning is emitted when
no categories are found so the operator knows to run that command first.

**Phase 2** – Fetch the list of accounts from the SAP ledger via ODBC
and create any missing ``Account`` records, assigning each one to its
corresponding subcategory.

Account classification uses **iniciales (prefix) matching**: the command
reads the level-2 ``AccountClassification`` code-mapping records (written
by ``load_categories_from_excel``) and tries progressively shorter
prefixes of each incoming account code until a match is found.  This
means accounts whose codes were not listed verbatim in the Excel are
still classified correctly as long as they share a common prefix with a
stored code.
"""

from django.core.management.base import BaseCommand

from accounts.models import Account, AccountClassification

MAX_CODE_LENGTH = AccountClassification._meta.get_field("code").max_length


class Command(BaseCommand):
    help = (
        "Sincroniza el maestro de cuentas del libro SAP para una sociedad "
        "(1000 o 1100).  Fase 1: lee las categorías y subcategorías desde la "
        "base de datos (deben haber sido cargadas con load_categories_from_excel).  "
        "Fase 2: carga cuentas desde SAP y las clasifica por iniciales."
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

        # ── Phase 1: Read category & subcategory classifications from DB ──
        self.stdout.write(
            f"Fase 1 – Leyendo categorías y subcategorías desde la base de "
            f"datos para sociedad {sociedad}…"
        )
        puc_schema = self._read_puc_schema_from_db(sociedad)

        if not puc_schema:
            self.stdout.write(
                self.style.WARNING(
                    f"No se encontraron mapeos de código para sociedad "
                    f"{sociedad} en la base de datos.  "
                    f"Ejecute primero: "
                    f"python manage.py load_categories_from_excel {sociedad} <archivo.xlsx>"
                )
            )
        else:
            self.stdout.write(
                f"  {len(puc_schema)} mapeos de código cargados desde la DB."
            )

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
    # Phase 1 – Read PUC schema from DB (built by load_categories_from_excel)
    # ------------------------------------------------------------------
    def _read_puc_schema_from_db(
        self,
        sociedad: str,
    ) -> dict[str, tuple[str, str]]:
        """Build a ``{account_code: (cat, subcat)}`` dict from the level-2
        ``AccountClassification`` records that were persisted by the
        ``load_categories_from_excel`` command.

        Returns an empty dict if no mappings exist yet.
        """
        mappings = AccountClassification.objects.filter(
            level=2,
            sociedad=sociedad,
        ).values("code", "cat", "subcat")
        return {m["code"]: (m["cat"], m["subcat"]) for m in mappings}

    # ------------------------------------------------------------------
    # Phase 1 – Classification sync (kept for backward-compatibility /
    # standalone use when categories need to be created from a schema dict)
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
        the ``cat`` / ``subcat`` fields on the level-1
        ``AccountClassification`` records.  The category tree **must**
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

            # Retrieve the subcategory classification (level=1 only)
            subcategoria = AccountClassification.objects.filter(
                cat=nombre_cat,
                subcat=nombre_subcat,
                sociedad=sociedad,
                level=1,
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
    # Category / subcategory lookup – iniciales (prefix) matching
    # ------------------------------------------------------------------
    @staticmethod
    def asignar_categoria_subcategoria(
        codigo: str,
        puc_schema: dict[str, tuple[str, str]],
    ) -> tuple[str | None, str | None]:
        """Look up an account code in the PUC schema using iniciales
        (prefix) matching and return ``(category_name, subcategory_name)``.

        The lookup strategy is:

        1. Try an exact match for ``codigo``.
        2. If not found, try progressively shorter prefixes of ``codigo``
           (from ``len(codigo) - 1`` down to 1) until a stored code is
           found whose value is used as the classification.

        This allows accounts whose full codes are not listed in the Excel
        to still be classified when they share an initial prefix with a
        stored code.

        Returns ``(None, None)`` when no match is found at any prefix
        length.
        """
        codigo = codigo.strip()
        # Exact match
        result = puc_schema.get(codigo)
        if result is not None:
            return result
        # Prefix (iniciales) matching – try shorter prefixes
        for length in range(len(codigo) - 1, 0, -1):
            prefix = codigo[:length]
            result = puc_schema.get(prefix)
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
