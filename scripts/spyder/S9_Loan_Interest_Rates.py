import requests
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime


URL = "https://sbi.bank.in/web/interest-rates/interest-rates/mclr-historical-data"


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


def _parse_site_date(date_text: str) -> datetime | None:
    """Parse SBI effective dates like '15.05.2026' (dd.mm.yyyy)."""
    text = date_text.strip()
    try:
        return datetime.strptime(text, "%d.%m.%Y")
    except ValueError:
        return None


def fetch_sbi_1yr_mclr_for_month(month: str, year: int | None = None) -> dict | None:
    """
    Fetch SBI 1Y MCLR and return only the rate for the given month.
    Site dates are dd.mm.yyyy (e.g. 15.05.2026). If multiple effective dates
    fall in that month, the latest one is returned.
    """
    response = requests.get(URL, timeout=60)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")

    tab = soup.find("div", {"id": "menu_0"})
    if not tab:
        raise RuntimeError("Could not find MCLR tab with id menu_0")

    table = tab.find("table")
    if not table:
        raise RuntimeError("Could not find table in MCLR tab")

    target_month = _parse_input_month(month)
    matches = []

    for row in table.find_all("tr"):
        cols = row.find_all(["td", "th"])
        cols_text = [col.get_text(strip=True) for col in cols]
        # Skip headers; keep rows with Effective Date + 7 periods
        if len(cols_text) != 8 or not cols_text[0] or "." not in cols_text[0]:
            continue

        eff_date = _parse_site_date(cols_text[0])
        if eff_date is None:
            continue

        # Match on the mm component of dd.mm.yyyy
        if eff_date.month != target_month:
            continue
        if year is not None and eff_date.year != year:
            continue

        matches.append({
            "date": cols_text[0],
            "1Y_MCLR": cols_text[5],
            "_parsed": eff_date,
        })

    if not matches:
        return None

    # Latest effective date in the requested month
    best = max(matches, key=lambda r: r["_parsed"])
    return {"date": best["date"], "1Y_MCLR": best["1Y_MCLR"]}


def main():
    month = "May"
    date = "May 2026"
    scriptID = "S7"
    staging_kind = "StagingData_v1"
    granularity = "Monthly"
    property_name = "dataName"
    dataName = "Marginal Cost of Funds based Lending Rate (MCLR) - 1 Year"

    year = int(date.split()[-1]) if date.strip().split()[-1].isdigit() else None
    record = fetch_sbi_1yr_mclr_for_month(month, year=year)
    if record is None:
        print(f"No 1Y MCLR found for {date}")
        return

    df = pd.DataFrame([record])
    print(df)


if __name__ == "__main__":
    main()
