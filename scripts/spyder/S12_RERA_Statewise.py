import calendar
import csv
import io
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import openpyxl

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

DOWNLOADS_DIR = Path(__file__).resolve().parent / "downloads"
RERA_XLSX = DOWNLOADS_DIR / "RERA.xlsx"

# 1-based Excel columns
COL_STATE = 3            # C – State / UT
COL_NO_OF_PROJECTS = 6   # F – No. of Project
COL_NO_OF_RES_UNITS = 7  # G – No. of Res. Unit
COL_AS_ON_DATE = 12      # L – As On Date
HEADER_ROWS = 3
CSV_FIELDS = ["item_name", "value1", "value2", "as_on_date", "month_end_date"]

# Exact / known aliases applied before generic cleanups.
STATE_NAME_ALIASES = {
    "haryana-gurugram": "Haryana",
    "a&n island": "Andaman and Nicobar Islands",
    "a&n island (ut)": "Andaman and Nicobar Islands",
    "andaman & nicobar islands": "Andaman and Nicobar Islands",
    "ut of dnh & dd": "Dadra and Nagar Haveli and Daman and Diu",
    "dnh & dd": "Dadra and Nagar Haveli and Daman and Diu",
}


def standardize_state_name(name: str) -> str:
    """
    Standardize RERA State/UT labels for administrative lookup tables:
    - Expand & -> and (e.g. Jammu and Kashmir)
    - Map UT of DNH & DD -> Dadra and Nagar Haveli and Daman and Diu
    - Strip trailing (UT) markers
    - Map Haryana-Gurugram -> Haryana (Gurugram is district-level)
    """
    text = " ".join(str(name or "").split())
    if not text:
        return ""

    alias = STATE_NAME_ALIASES.get(text.casefold())
    if alias:
        return alias

    # Suffix removal: "Delhi (UT)" -> "Delhi"
    text = re.sub(r"\s*\(\s*UT\s*\)\s*$", "", text, flags=re.IGNORECASE).strip()
    alias = STATE_NAME_ALIASES.get(text.casefold())
    if alias:
        return alias

    # Ampersands expanded: "Jammu & Kashmir" -> "Jammu and Kashmir"
    text = re.sub(r"\s*&\s*", " and ", text)
    text = " ".join(text.split())

    alias = STATE_NAME_ALIASES.get(text.casefold())
    if alias:
        return alias

    return text


def _cell_str(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_number(value):
    """Keep numeric Excel values; leave non-numeric text as-is (or blank)."""
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value
    text = str(value).strip()
    if not text:
        return ""
    try:
        num = float(text.replace(",", ""))
        return int(num) if num.is_integer() else num
    except ValueError:
        return text


def _format_as_on_date(value) -> str:
    """Normalize Excel As On Date to DD-MM-YYYY (or blank / raw text)."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d-%m-%Y")
    text = str(value).strip()
    if not text or text == "-":
        return ""
    return text


def _month_end_date(as_on_date: str) -> str:
    """Convert DD-MM-YYYY as_on_date to last day of that month (same format)."""
    if not as_on_date:
        return ""
    try:
        date_obj = datetime.strptime(as_on_date, "%d-%m-%Y")
    except ValueError:
        return ""
    last_day = calendar.monthrange(date_obj.year, date_obj.month)[1]
    return f"{last_day:02d}-{date_obj.month:02d}-{date_obj.year}"


def fetch_rera_statewise(xlsx_path: Path | None = None) -> list[dict]:
    """
    Read RERA.xlsx and return one row per state/UT:
      item_name (col C), value1 = No. of Projects (col F),
      value2 = No. of Res Units (col G), as_on_date (col L),
      month_end_date = last day of as_on_date's month.
    Skips header / subtotal / grand-total rows (empty column C).
    """
    path = xlsx_path or RERA_XLSX
    if not path.exists():
        raise FileNotFoundError(f"RERA Excel not found at {path}")

    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active

    rows: list[dict] = []
    for row in ws.iter_rows(min_row=HEADER_ROWS + 1, values_only=True):
        state = _cell_str(row[COL_STATE - 1] if len(row) >= COL_STATE else None)
        if not state:
            continue
        as_on_date = _format_as_on_date(
            row[COL_AS_ON_DATE - 1] if len(row) >= COL_AS_ON_DATE else None
        )
        rows.append(
            {
                "item_name": standardize_state_name(state),
                "value1": _to_number(
                    row[COL_NO_OF_PROJECTS - 1] if len(row) >= COL_NO_OF_PROJECTS else None
                ),
                "value2": _to_number(
                    row[COL_NO_OF_RES_UNITS - 1]
                    if len(row) >= COL_NO_OF_RES_UNITS
                    else None
                ),
                "as_on_date": as_on_date,
                "month_end_date": _month_end_date(as_on_date),
            }
        )
    return rows


def rows_to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def main():
    month = "Jun"
    date = f"{month} 2026"
    scriptID = "S12"
    staging_kind = "StagingData_v1"
    granularity = "Monthly"
    property_name = "dataName"
    dataName = "Registered Residential Prjects under RERA"

    rows = fetch_rera_statewise()
    print(rows_to_csv(rows), end="")
    print(f"# {len(rows)} states/UTs from {RERA_XLSX.name} ({date})", file=sys.stderr)


if __name__ == "__main__":
    main()
