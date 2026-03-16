"""
Utilities for reading and validating PUC Excel files.

Each company's Excel has a sheet with columns for category names,
subcategory names, account codes, and account descriptions.  This module
extracts that information into data structures that can be consumed by
management commands to create classifications and accounts.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field

# Column names that MUST be present in the first row of the sheet.
_REQUIRED_COLUMNS = frozenset(
    {
        "Nombre Categoría",
        "Nombre Subcategoría",
        "Nombre Cuenta",
        "Descripción Cuenta",
    }
)

# The first sheet in each workbook is expected to hold the PUC schema.
_DEFAULT_SHEET_INDEX = 0


# ------------------------------------------------------------------
# Public data structures
# ------------------------------------------------------------------
@dataclass
class ExcelRow:
    """One validated row from the PUC Excel file."""

    category: str
    subcategory: str
    account_code: str
    account_description: str


@dataclass
class ExcelValidationResult:
    """Result of reading and validating a PUC Excel file."""

    path: str
    rows: list[ExcelRow] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0


# ------------------------------------------------------------------
# Public helpers
# ------------------------------------------------------------------
def read_and_validate_excel(filepath: str | pathlib.Path) -> ExcelValidationResult:
    """Read a PUC Excel file and return validated rows.

    Validates that:
    * The file exists and is a valid ``.xlsx`` workbook.
    * Required columns are present.
    * Each data row has non-empty category, subcategory, and account code.

    Returns an :class:`ExcelValidationResult` with ``rows``, ``errors``
    and ``warnings``.
    """
    try:
        import openpyxl  # noqa: WPS433
    except ImportError:
        return ExcelValidationResult(
            path=str(filepath),
            errors=[
                "openpyxl is not installed.  Run: pip install openpyxl",
            ],
        )

    filepath = pathlib.Path(filepath)
    result = ExcelValidationResult(path=str(filepath))

    if not filepath.exists():
        result.errors.append(f"File not found: {filepath}")
        return result

    if filepath.suffix.lower() not in (".xlsx", ".xlsm"):
        result.errors.append(
            f"Unsupported file format '{filepath.suffix}'.  Only .xlsx is supported."
        )
        return result

    # Open the workbook
    try:
        wb = openpyxl.load_workbook(str(filepath), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"Cannot open workbook: {exc}")
        return result

    try:
        ws = wb.worksheets[_DEFAULT_SHEET_INDEX]
        all_rows = list(ws.iter_rows(values_only=True))
    finally:
        wb.close()

    if not all_rows:
        result.errors.append("The first sheet is empty.")
        return result

    # --- Validate headers --------------------------------------------------
    headers = [str(h).strip() if h is not None else "" for h in all_rows[0]]
    header_set = set(headers)
    missing = _REQUIRED_COLUMNS - header_set
    if missing:
        result.errors.append(
            f"Missing required columns: {sorted(missing)}.  "
            f"Found: {headers}"
        )
        return result

    col_index = {name: idx for idx, name in enumerate(headers)}
    idx_cat = col_index["Nombre Categoría"]
    idx_subcat = col_index["Nombre Subcategoría"]
    idx_code = col_index["Nombre Cuenta"]
    idx_desc = col_index["Descripción Cuenta"]

    # --- Parse data rows ---------------------------------------------------
    for row_num, raw_row in enumerate(all_rows[1:], start=2):
        cat = _cell_str(raw_row, idx_cat)
        subcat = _cell_str(raw_row, idx_subcat)
        code = _cell_str(raw_row, idx_code)
        desc = _cell_str(raw_row, idx_desc)

        if not cat and not subcat and not code:
            # Fully empty row – skip silently
            continue

        if not cat:
            result.warnings.append(f"Row {row_num}: empty category – skipped.")
            continue
        if not subcat:
            result.warnings.append(f"Row {row_num}: empty subcategory – skipped.")
            continue
        if not code:
            result.warnings.append(f"Row {row_num}: empty account code – skipped.")
            continue

        # Skip placeholder values (e.g. "0") that are not real classifications
        if cat.isdigit():
            result.warnings.append(
                f"Row {row_num}: numeric category '{cat}' – skipped."
            )
            continue
        if subcat.isdigit():
            result.warnings.append(
                f"Row {row_num}: numeric subcategory '{subcat}' – skipped."
            )
            continue

        # Normalise the account code (may be a number in some sheets)
        code = code.lstrip("0") or "0"  # keep at least "0"
        code = code.zfill(10)  # pad back to 10 digits

        result.rows.append(
            ExcelRow(
                category=cat,
                subcategory=subcat,
                account_code=code,
                account_description=desc,
            )
        )

    if not result.rows:
        result.errors.append("No valid data rows found in the Excel file.")

    return result


def build_puc_schema(
    rows: list[ExcelRow],
) -> dict[str, tuple[str, str]]:
    """Build a PUC schema dict ``{account_code: (category, subcategory)}``
    from validated Excel rows.

    This produces the same structure as the hard-coded schemas in
    ``puc_schemas.py`` so it can be used interchangeably.
    """
    schema: dict[str, tuple[str, str]] = {}
    for row in rows:
        schema[row.account_code] = (row.category, row.subcategory)
    return schema


def build_category_tree(
    rows: list[ExcelRow],
) -> dict[str, set[str]]:
    """Build ``{category: {subcategory, …}}`` from validated Excel rows."""
    tree: dict[str, set[str]] = {}
    for row in rows:
        tree.setdefault(row.category, set()).add(row.subcategory)
    return tree


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------
def _cell_str(row: tuple, idx: int) -> str:
    """Return a stripped string from a cell, handling None and numeric values."""
    if idx >= len(row):
        return ""
    val = row[idx]
    if val is None:
        return ""
    return str(val).strip()
