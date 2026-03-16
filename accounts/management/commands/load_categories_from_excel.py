"""
Management command: load_categories_from_excel

Validates a PUC Excel file and creates the category / subcategory
AccountClassification records for the given *sociedad*.  This is
the **first step** in the initial load: categories are created from the
Excel reference so that new accounts coming from SAP can be immediately
classified.

This command is responsible **only** for loading the category hierarchy
(cat / subcat) from the Excel file.  It also persists each account-code
to (cat, subcat) mapping as a level-2 ``AccountClassification`` record
so that the PUC loading command can classify accounts by their initials
(prefix matching) without relying on a hard-coded schema file.

Usage::

    python manage.py load_categories_from_excel 1000 Esquema_Cuentas_PUC_1000.xlsx
    python manage.py load_categories_from_excel 1100 "Esquema_Cuentas_PUC_1100 (ES).xlsx"
"""

from django.core.management.base import BaseCommand, CommandError

from accounts.excel_utils import (
    build_category_tree,
    build_puc_schema,
    read_and_validate_excel,
)
from accounts.models import AccountClassification

MAX_CODE_LENGTH = AccountClassification._meta.get_field("code").max_length


class Command(BaseCommand):
    help = (
        "Valida un archivo Excel PUC y crea las categorías y subcategorías "
        "(AccountClassification) para la sociedad indicada.  "
        "Este es el primer paso del cargue inicial."
    )

    # ------------------------------------------------------------------
    # Arguments
    # ------------------------------------------------------------------
    def add_arguments(self, parser):
        parser.add_argument(
            "sociedad",
            type=str,
            help="Código de sociedad (e.g. 1000 o 1100).",
        )
        parser.add_argument(
            "excel_file",
            type=str,
            help="Ruta al archivo Excel (.xlsx) con el esquema PUC.",
        )
        parser.add_argument(
            "--validate-only",
            action="store_true",
            default=False,
            help="Solo valida el Excel sin crear registros en la base de datos.",
        )
        parser.add_argument(
            "--compare-schema",
            action="store_true",
            default=False,
            help=(
                "Compara el Excel contra el esquema estático en puc_schemas.py "
                "y reporta diferencias."
            ),
        )

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------
    def handle(self, *args, **kwargs):
        sociedad = kwargs["sociedad"]
        excel_path = kwargs["excel_file"]
        validate_only = kwargs["validate_only"]
        compare_schema = kwargs["compare_schema"]

        # Step 1 – Read and validate the Excel file
        self.stdout.write(f"Leyendo archivo: {excel_path}")
        result = read_and_validate_excel(excel_path)

        # Report warnings
        for w in result.warnings:
            self.stdout.write(self.style.WARNING(w))

        if not result.is_valid:
            for e in result.errors:
                self.stdout.write(self.style.ERROR(e))
            raise CommandError(
                "El archivo Excel no es válido.  Corrija los errores e intente de nuevo."
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Excel válido: {len(result.rows)} filas de datos leídas."
            )
        )

        # Build the category tree
        tree = build_category_tree(result.rows)
        total_subcats = sum(len(v) for v in tree.values())
        self.stdout.write(
            f"Categorías únicas: {len(tree)}, "
            f"Subcategorías únicas: {total_subcats}"
        )

        # Step 2 – Optional: compare against puc_schemas.py
        if compare_schema:
            self._compare_with_static_schema(sociedad, result)

        if validate_only:
            self._print_tree(tree)
            self.stdout.write(
                self.style.SUCCESS("Validación completada (sin cambios en DB).")
            )
            return

        # Step 3 – Create categories and subcategories
        cats_created, subcats_created = self._create_classifications(
            sociedad, tree,
        )

        # Step 4 – Persist account-code → (cat, subcat) mappings as level-2
        # records so that the PUC loading command can classify accounts by
        # their initials (prefix) without relying on a hard-coded schema file.
        puc_schema = build_puc_schema(result.rows)
        codes_created = self._create_code_mappings(sociedad, puc_schema)

        self.stdout.write(
            self.style.SUCCESS(
                f"Sincronización completada para sociedad {sociedad}: "
                f"{cats_created} categorías creadas, "
                f"{subcats_created} subcategorías creadas, "
                f"{codes_created} mapeos de código creados."
            )
        )

    # ------------------------------------------------------------------
    # Create classification hierarchy
    # ------------------------------------------------------------------
    def _create_classifications(
        self,
        sociedad: str,
        tree: dict[str, set[str]],
    ) -> tuple[int, int]:
        """Create category (level 0) and subcategory (level 1) records.

        Returns ``(categories_created, subcategories_created)``.
        """
        cats_created = 0
        subcats_created = 0

        for nombre_cat, subcats in sorted(tree.items()):
            cat_code = nombre_cat[:MAX_CODE_LENGTH]
            categoria, created = AccountClassification.objects.get_or_create(
                code=cat_code,
                sociedad=sociedad,
                defaults={
                    "name": nombre_cat,
                    "level": 0,
                    "cat": nombre_cat,
                },
            )
            if created:
                cats_created += 1
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
                    subcats_created += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  Subcategoría creada: {nombre_subcat} "
                            f"(categoría: {nombre_cat})"
                        )
                    )

        return cats_created, subcats_created

    # ------------------------------------------------------------------
    # Persist account-code mappings (level 2)
    # ------------------------------------------------------------------
    def _create_code_mappings(
        self,
        sociedad: str,
        puc_schema: dict[str, tuple[str, str]],
    ) -> int:
        """Create level-2 AccountClassification records that map each
        account code from the Excel to its (cat, subcat) pair.

        These records are later consumed by the PUC loading command
        (``taskcreacioncuentascategoria``) to classify accounts by their
        initials (prefix matching) without relying on a hard-coded schema.

        Returns the number of new mapping records created.
        """
        codes_created = 0
        for account_code, (nombre_cat, nombre_subcat) in puc_schema.items():
            # Look up the parent subcategory (level=1) for this mapping
            cat_code = nombre_cat[:MAX_CODE_LENGTH]
            sub_code = f"{cat_code}:{nombre_subcat}"[:MAX_CODE_LENGTH]
            try:
                parent_subcat = AccountClassification.objects.get(
                    code=sub_code,
                    sociedad=sociedad,
                    level=1,
                )
            except AccountClassification.DoesNotExist:
                self.stdout.write(
                    self.style.WARNING(
                        f"Subcategoría '{nombre_subcat}' no encontrada para "
                        f"código {account_code} – omitido."
                    )
                )
                continue

            _mapping, created = AccountClassification.objects.get_or_create(
                code=account_code[:MAX_CODE_LENGTH],
                sociedad=sociedad,
                defaults={
                    "name": account_code,
                    "parent": parent_subcat,
                    "level": 2,
                    "cat": nombre_cat,
                    "subcat": nombre_subcat,
                },
            )
            if created:
                codes_created += 1

        return codes_created

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _print_tree(self, tree: dict[str, set[str]]) -> None:
        """Pretty-print the category tree to stdout."""
        for cat, subcats in sorted(tree.items()):
            self.stdout.write(f"  {cat}")
            for sub in sorted(subcats):
                self.stdout.write(f"    └─ {sub}")

    def _compare_with_static_schema(self, sociedad, result) -> None:
        """Compare Excel data against the hard-coded PUC schema."""
        try:
            from accounts.puc_schemas import get_puc_schema

            static_schema = get_puc_schema(sociedad)
        except ValueError:
            self.stdout.write(
                self.style.WARNING(
                    f"No hay esquema estático para sociedad {sociedad}."
                )
            )
            return

        excel_schema = build_puc_schema(result.rows)

        only_in_excel = set(excel_schema.keys()) - set(static_schema.keys())
        only_in_static = set(static_schema.keys()) - set(excel_schema.keys())
        common = set(excel_schema.keys()) & set(static_schema.keys())
        mismatched = {
            code
            for code in common
            if excel_schema[code] != static_schema[code]
        }

        if only_in_excel:
            self.stdout.write(
                self.style.WARNING(
                    f"Cuentas solo en Excel ({len(only_in_excel)}): "
                    f"{sorted(only_in_excel)[:10]}…"
                )
            )
        if only_in_static:
            self.stdout.write(
                self.style.WARNING(
                    f"Cuentas solo en esquema estático ({len(only_in_static)}): "
                    f"{sorted(only_in_static)[:10]}…"
                )
            )
        if mismatched:
            self.stdout.write(
                self.style.WARNING(
                    f"Cuentas con categorización diferente ({len(mismatched)}):"
                )
            )
            for code in sorted(mismatched)[:10]:
                self.stdout.write(
                    f"  {code}: Excel={excel_schema[code]} vs "
                    f"Estático={static_schema[code]}"
                )

        if not only_in_excel and not only_in_static and not mismatched:
            self.stdout.write(
                self.style.SUCCESS(
                    "El Excel coincide con el esquema estático."
                )
            )
