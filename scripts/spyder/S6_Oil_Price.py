import requests
import pandas as pd
from bs4 import BeautifulSoup

BASE_URL = "https://ppac.gov.in/prices"
AJAX_URL = "https://ppac.gov.in/AjaxController/getInternationalPricesCrudeOil"

# Matches the <ul class="list-group list-group-horizontal cpd-tab"> tab links
TAB_CONFIG = {
    "CrudeOil": {
        "path": "international-prices-of-crude-oil",
        "page_id": "30",
        "label": "Crude Oil FOB Price (Indian Basket)",
    },
    "Petrol": {
        "path": "international-prices-of-petrol",
        "page_id": "48",
        "label": "Petrol (FOB) International Price",
    },
    "Desiel": {
        "path": "international-prices-of-diesel",
        "page_id": "47",
        "label": "Diesel (FOB) International Price",
    },
}

MONTH_TO_FIELD = {
    "april": "april",
    "may": "may",
    "june": "june",
    "july": "july",
    "august": "august",
    "september": "september",
    "october": "october",
    "november": "november",
    "december": "december",
    "january": "january",
    "february": "february",
    "march": "march",
}


def _get_default_financial_year(tab_path: str) -> str:
    """Read the latest financial year from the page dropdown."""
    response = requests.get(f"{BASE_URL}/{tab_path}", timeout=60)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")
    fy_select = soup.find("select", id="financialYear")
    if not fy_select:
        raise RuntimeError("financialYear dropdown not found")

    years = [
        opt.get("value", "").strip()
        for opt in fy_select.find_all("option")
        if opt.get("value", "").strip()
    ]
    if not years:
        raise RuntimeError("No financial year options found")
    return years[-1]


def fetch_ppac_table_data(
    table_name: str,
    month: str = "July",
    financial_year: str | None = None,
) -> pd.DataFrame:
    """
    Fetches price data for the given tab. The visible table is built by JS via AJAX;
    static HTML only has headers in <tbody id="reportList">.
    """
    if table_name not in TAB_CONFIG:
        raise ValueError(f"Unknown table: {table_name}")

    month_key = month.strip().lower()
    if month_key not in MONTH_TO_FIELD:
        raise ValueError(f"Unknown month: {month}")

    cfg = TAB_CONFIG[table_name]
    fy = financial_year or _get_default_financial_year(cfg["path"])

    response = requests.post(
        AJAX_URL,
        data={
            "financialYear": fy,
            "reportBy": "4",
            "pageId": cfg["page_id"],
        },
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("result") or {}

    records = []
    for item in rows.values():
        year = str(item.get("title", "")).strip()
        if not year or not year[0].isdigit():
            continue

        value = item.get(MONTH_TO_FIELD[month_key], "")
        if value in ("", None):
            continue

        records.append({"Year": year, month.strip().title(): value})

    return pd.DataFrame(records)


def main():
    date = "July 2026" # date of the new data
    month = "July" # date of the new data
    kind = "TimeSeriesData" # to fetch old items from DB
    staging_kind = "StagingData_v1" # to update the data in DB
    granularity = "Monthly" # granularity of the data
    scriptID = "S6" # scriptID of the data
    property_name = "dataName" # to fetch old items from DB
    dataName = "International Prices of Crude Oil, Petrol and Diesel" # to fetch old items & Update from DB
    pdf_path = f"https://ppac.gov.in/prices/international-prices-of-crude-oil"
    

    for tab in ["CrudeOil", "Petrol", "Desiel"]:
        print(f"\n===== {tab} ({TAB_CONFIG[tab]['label']}) - {month} =====")
        try:
            df = fetch_ppac_table_data(tab, month=month)
            print(df.to_string(index=False))
        except Exception as e:
            print(f"Error fetching {tab}: {e}")


if __name__ == "__main__":
    main()
