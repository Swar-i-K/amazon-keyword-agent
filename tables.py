"""
tables.py
---------
Writes the agent's tables as one Excel workbook (.xlsx, one sheet per table) or as separate CSV files.
Format comes from `output_format` in config.yaml: xlsx (default), csv, or both.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

FONT = "Arial"
HEADER_FILL = PatternFill("solid", start_color="1F3864")
WRAP_OVER = 38          # columns whose longest text is wider than this wrap instead of stretching
MAX_WIDTH = 60


def output_format(cfg: dict) -> str:
    fmt = str((cfg or {}).get("output_format", "xlsx")).strip().lower()
    return fmt if fmt in ("xlsx", "csv", "both") else "xlsx"


def _sheet_name(name: str, used: set) -> str:
    base = re.sub(r"[\[\]:*?/\\]", " ", name).strip()[:31] or "Sheet"
    n, i = base, 2
    while n in used:
        n = f"{base[:28]} {i}"
        i += 1
    used.add(n)
    return n


def write_workbook(path: Path, sheets: list) -> None:
    """sheets: [(sheet_name, headers, rows)], each row a list in header order. Numbers stay numbers."""
    wb = Workbook()
    wb.remove(wb.active)
    used = set()
    for name, headers, rows in sheets:
        ws = wb.create_sheet(_sheet_name(name, used))
        ws.append(list(headers))
        for r in rows:
            ws.append(list(r))
        for c in ws[1]:
            c.font = Font(name=FONT, size=10, bold=True, color="FFFFFF")
            c.fill = HEADER_FILL
            c.alignment = Alignment(vertical="center", wrap_text=True)
        for ci in range(1, len(headers) + 1):
            longest = len(str(headers[ci - 1]))
            for row in ws.iter_rows(min_row=2, min_col=ci, max_col=ci):
                cell = row[0]
                v = cell.value
                if isinstance(v, str) and v.startswith("="):       # keep text that starts with = as text, not a formula
                    cell.data_type = "s"
                cell.font = Font(name=FONT, size=10)
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                longest = max(longest, max((len(x) for x in str(v if v is not None else "").split("\n")), default=0))
            ws.column_dimensions[get_column_letter(ci)].width = min(MAX_WIDTH, max(10, longest + 2)) if longest <= WRAP_OVER else MAX_WIDTH
        ws.freeze_panes = "A2"
        if rows:
            ws.auto_filter.ref = ws.dimensions
    wb.save(path)


def write_csv(path: Path, headers: list, rows: list) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)


def write_tables(out: Path, workbook_name: str, tables: list, cfg: dict) -> list:
    """tables: [(sheet_name, csv_file_name, headers, rows)]. Returns the file names written."""
    fmt, written = output_format(cfg), []
    if fmt in ("xlsx", "both"):
        write_workbook(out / workbook_name, [(s, h, r) for s, _, h, r in tables])
        written.append(workbook_name)
    if fmt in ("csv", "both"):
        for _, csv_name, h, r in tables:
            write_csv(out / csv_name, h, r)
            written.append(csv_name)
    return written
