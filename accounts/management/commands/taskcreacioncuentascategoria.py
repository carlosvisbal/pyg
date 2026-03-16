from django.core.management.base import BaseCommand

from accounts.models import Account, AccountClassification
from accounts.puc_schemas import get_puc_schema


class Command(BaseCommand):
    help = (
        "Sincroniza el maestro de cuentas del libro SAP para una sociedad "
        "(1000 o 1100). Crea las categorías, subcategorías y cuentas "
        "contables a partir del esquema PUC definido en puc_schemas.py."
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

        # Step 1 – Ensure category & subcategory classifications exist
        self._sync_classifications(sociedad, puc_schema)

        # Step 2 – Fetch accounts from SAP and create any missing ones
        cuentas = self._lista_cuentas_sap(sociedad)
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
                        f"Sin esquema para la cuenta {codigo} en sociedad {sociedad}"
                    )
                )
                sin_asignar += 1
                continue

            # Retrieve the subcategory classification
            subcategoria = AccountClassification.objects.filter(
                name=nombre_subcat,
                sociedad=sociedad,
                parent__name=nombre_cat,
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
                    self.style.SUCCESS(f"Cuenta creada: {codigo} - {descripcion}")
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Sincronización completada para sociedad {sociedad}: "
                f"{nuevas} cuentas nuevas, {sin_asignar} sin asignación."
            )
        )

    # ------------------------------------------------------------------
    # Classification sync
    # ------------------------------------------------------------------
    def _sync_classifications(self, sociedad, puc_schema):
        """Create category (level 0) and subcategory (level 1) records."""
        # Build {category: {subcategory, …}} from the schema
        tree: dict[str, set[str]] = {}
        for _code, (cat, subcat) in puc_schema.items():
            tree.setdefault(cat, set()).add(subcat)

        for nombre_cat, subcats in sorted(tree.items()):
            cat_code = nombre_cat[:50]
            categoria, cat_created = AccountClassification.objects.get_or_create(
                code=cat_code,
                sociedad=sociedad,
                defaults={
                    "name": nombre_cat,
                    "level": 0,
                },
            )
            if cat_created:
                self.stdout.write(
                    self.style.SUCCESS(f"Categoría creada: {nombre_cat}")
                )

            for nombre_subcat in sorted(subcats):
                sub_code = f"{cat_code}:{nombre_subcat}"[:50]
                _subcat, sub_created = AccountClassification.objects.get_or_create(
                    code=sub_code,
                    sociedad=sociedad,
                    defaults={
                        "name": nombre_subcat,
                        "parent": categoria,
                        "level": 1,
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
    # Category / subcategory lookup (data-driven)
    # ------------------------------------------------------------------
    @staticmethod
    def asignar_categoria_subcategoria(
        codigo: str,
        puc_schema: dict[str, tuple[str, str]],
    ) -> tuple[str | None, str | None]:
        """
        Look up an account code in the PUC schema and return
        (category_name, subcategory_name).

        Returns (None, None) when the code is not found.
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
                    "pyodbc no está instalado. No se pueden obtener cuentas de SAP."
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
        conn = pyodbc.connect(conn_str)
        try:
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
        finally:
            conn.close()
