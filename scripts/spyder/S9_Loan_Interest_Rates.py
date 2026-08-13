from datetime import datetime
from dateutil.relativedelta import relativedelta
from pathlib import Path
import os
import sys

import pandas as pd
import requests
from bs4 import BeautifulSoup

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from app.services.datastore_services import update_Datastore


URL = "https://sbi.bank.in/web/interest-rates/interest-rates/mclr-historical-data"

# Column positions in table.table-bordered data rows:
# Effective Date | ON | 1M | 3M | 6M | 1Y | 2Y | 3Y
COL_DATE = 0
COL_6M = 4
COL_3Y = 7

# Datastore item names (entity `item` property)
ITEM_6M = "6M"
ITEM_3Y = "3Y"


def _parse_input_month(month: str) -> int:
    """Accept month as name ('May'/'June') or numeric ('5'/'05')."""
    raw = month.strip()
    if raw.isdigit():
        value = int(raw)
        if 1 <= value <= 12:
            return value
        raise ValueError(f"Invalid month number: {month}")

    for fmt in ("%B", "%b"):
        try:
            return datetime.strptime(raw.title(), fmt).month
        except ValueError:
            continue
    raise ValueError(f"Unrecognized month: {month}")


def _parse_input_date(date: str) -> tuple[int, int]:
    """Parse 'May 2026' / 'May 26' style input into (month, year)."""
    parts = date.strip().split()
    if len(parts) < 2 or not parts[-1].isdigit():
        raise ValueError(f"Expected date like 'May 2026', got {date!r}")
    year = int(parts[-1])
    if year < 100:
        year += 2000
    month = _parse_input_month(" ".join(parts[:-1]))
    return month, year


def _parse_site_date(date_text: str) -> datetime | None:
    """Parse SBI effective dates like '15.05.2026' (dd.mm.yyyy)."""
    text = date_text.strip()
    try:
        return datetime.strptime(text, "%d.%m.%Y")
    except ValueError:
        return None


def _fetch_mclr_table_rows() -> list[dict]:
    """
    Scrape <table class=\"table table-bordered\"> from the SBI MCLR page.
    Returns one dict per data row with parsed date + 6M/3Y rates.
    """
    response = requests.get(URL, timeout=60)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")

    table = soup.select_one("table.table.table-bordered")
    if not table:
        # Fallback to the MCLR tab used previously
        tab = soup.find("div", {"id": "menu_0"})
        table = tab.find("table") if tab else None
    if not table:
        raise RuntimeError('Could not find <table class="table table-bordered">')

    rows = []
    for row in table.find_all("tr"):
        cols = row.find_all(["td", "th"])
        cols_text = [col.get_text(strip=True) for col in cols]
        # Effective Date + 7 tenor columns
        if len(cols_text) != 8 or not cols_text[0] or "." not in cols_text[0]:
            continue

        eff_date = _parse_site_date(cols_text[0])
        if eff_date is None:
            continue

        rows.append({
            "date": cols_text[0],
            "parsed": eff_date,
            "year": eff_date.year,
            "month": eff_date.month,
            ITEM_6M: cols_text[COL_6M],
            ITEM_3Y: cols_text[COL_3Y],
        })

    if not rows:
        raise RuntimeError("No MCLR data rows found in table")
    return rows


def _latest_per_month(rows: list[dict]) -> dict[tuple[int, int], dict]:
    """Keep the latest effective-date row for each (year, month)."""
    by_month: dict[tuple[int, int], dict] = {}
    for row in rows:
        key = (row["year"], row["month"])
        prev = by_month.get(key)
        if prev is None or row["parsed"] > prev["parsed"]:
            by_month[key] = row
    return by_month


def fetch_mclr_last_12_months(month: str, date: str) -> pd.DataFrame:
    """
    Fetch 6M and 3Y MCLR for the input month and the prior 11 months.

    Validates that the input month/date matches the latest month present
    on the site, to catch human typos when running once a month.
    """
    input_month = _parse_input_month(month)
    date_month, date_year = _parse_input_date(date)
    if input_month != date_month:
        raise ValueError(
            f"Month mismatch between month={month!r} and date={date!r}"
        )

    rows = _fetch_mclr_table_rows()
    by_month = _latest_per_month(rows)

    latest_row = max(rows, key=lambda r: r["parsed"])
    latest_key = (latest_row["year"], latest_row["month"])
    expected_key = (date_year, date_month)

    if latest_key != expected_key:
        latest_label = latest_row["parsed"].strftime("%B %Y")
        raise ValueError(
            f"Input date {date!r} does not match the latest month on the site "
            f"({latest_label}, effective {latest_row['date']}). "
            f"Update month/date before running."
        )

    records = []
    cursor = datetime(date_year, date_month, 1)
    for _ in range(12):
        key = (cursor.year, cursor.month)
        row = by_month.get(key)
        if row is None:
            raise RuntimeError(
                f"No MCLR row found for {cursor.strftime('%B %Y')} "
                f"(needed for last-12-months window ending {date})"
            )
        records.append({
            "month": cursor.strftime("%b %Y"),
            "date": row["date"],
            ITEM_6M: row[ITEM_6M],
            ITEM_3Y: row[ITEM_3Y],
            "_year": cursor.year,
            "_month": cursor.month,
        })
        cursor -= relativedelta(months=1)

    return pd.DataFrame(records)


def main():
    month = "May"
    date = "May 2025"
    scriptID = "S9"
    staging_kind = "StagingData_v1"
    granularity = "Monthly"
    property_name = "dataName"
    dataName = "Marginal Cost of Funds-Based Lending Rate in India"

    df = fetch_mclr_last_12_months(month=month, date=date)
    print(df[["month", "date", ITEM_6M, ITEM_3Y]].to_string(index=False))

    # Write each month separately so StagingData dateTime matches that month.
    for record in df.to_dict(orient="records"):
        month_label = record["month"]  # e.g. "May 2026"
        parsed = {
            ITEM_6M: record[ITEM_6M],
            ITEM_3Y: record[ITEM_3Y],
        }
        update_Datastore(
            parsed,
            month_label,
            granularity,
            scriptID,
            URL,
            dataName,
            staging_kind,
        )


if __name__ == "__main__":
    main()
