import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import pdfplumber
import requests
from bs4 import BeautifulSoup

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from utils import (
    fetch_item_names_from_csv_of_PublishedData_v1,
    get_gemini_api_key,
    parse_json_from_llm_output,
)
from app.services.datastore_services import update_Datastore

PRESS_RELEASE_URL = "https://fada.in/press-release-list.php"
DOWNLOADS_DIR = Path(__file__).resolve().parent / "downloads"
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# FADA OEM table header col 2 looks like Apr'26 / Apr'2026
FADA_HEADER_DATE_RE = re.compile(
    r"([A-Za-z]{3,9})\s*['’]?\s*(\d{2,4})", re.IGNORECASE
)
RETAIL_TITLE_RE = re.compile(
    r"^FADA\s+Releases\s+(.+?)\s+(\d{4})\s+Vehicle\s+Retail\s+Data$",
    re.IGNORECASE,
)


def _parse_input_date(date: str) -> tuple[int, int]:
    """Parse input date like 'Apr 2026' / 'April 26' into (month, year)."""
    parts = date.strip().split()
    if len(parts) < 2 or not parts[-1].isdigit():
        raise ValueError(f"Expected date like 'Apr 2026', got {date!r}")
    year = int(parts[-1])
    if year < 100:
        year += 2000
    month_raw = " ".join(parts[:-1])
    for fmt in ("%B", "%b"):
        try:
            return datetime.strptime(month_raw.title(), fmt).month, year
        except ValueError:
            continue
    raise ValueError(f"Unrecognized month in date: {date!r}")


def _month_full_name(month: int) -> str:
    return datetime(2000, month, 1).strftime("%B")


def _normalize_title(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _title_matches_retail_release(title: str, month: int, year: int) -> bool:
    """True when card title is 'FADA Releases {Month} {Year} Vehicle Retail Data'."""
    match = RETAIL_TITLE_RE.match(_normalize_title(title))
    if not match:
        return False
    title_month_raw, title_year = match.group(1), int(match.group(2))
    if title_year != year:
        return False
    for fmt in ("%B", "%b"):
        try:
            return datetime.strptime(title_month_raw.title(), fmt).month == month
        except ValueError:
            continue
    return False


def download_fada_retail_pdf(
    date: str,
    download_dir: Path | None = None,
    max_pages: int = 6,
) -> Path:
    """
    Scrape https://fada.in/press-release-list.php for the press-release card whose
    title is 'FADA Releases {Month} {Year} Vehicle Retail Data', then download the
    PDF from the card's 'Download' button (a.btn.btn-primary.btn-sm).
    """
    month, year = _parse_input_date(date)
    expected_title = (
        f"FADA Releases {_month_full_name(month)} {year} Vehicle Retail Data"
    )
    save_dir = download_dir or DOWNLOADS_DIR
    save_dir.mkdir(parents=True, exist_ok=True)

    for page_num in range(1, max_pages + 1):
        list_url = f"{PRESS_RELEASE_URL}?page={page_num}"
        response = requests.get(list_url, headers=REQUEST_HEADERS, timeout=60)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")

        for card in soup.select("div.card-body"):
            h3 = card.select_one("h3.font-weight-semibold") or card.select_one("h3")
            if not h3:
                continue
            title = _normalize_title(h3.get_text(" ", strip=True))
            if not _title_matches_retail_release(title, month, year):
                continue

            download_a = card.select_one("a.btn.btn-primary.btn-sm.mt-4") or card.select_one(
                "a.btn.btn-primary"
            )
            if not download_a or not download_a.get("href"):
                raise RuntimeError(
                    f"Found title {title!r} but no Download button on {list_url}"
                )

            pdf_url = urljoin(PRESS_RELEASE_URL, download_a["href"])
            pdf_response = requests.get(
                pdf_url,
                headers={**REQUEST_HEADERS, "Referer": PRESS_RELEASE_URL},
                timeout=120,
            )
            if pdf_response.status_code != 200:
                raise RuntimeError(
                    f"Download failed for {title!r}: HTTP {pdf_response.status_code} "
                    f"from {pdf_url}"
                )
            if not pdf_response.content.startswith(b"%PDF"):
                raise RuntimeError(
                    f"Download for {title!r} did not return a PDF "
                    f"(content-type={pdf_response.headers.get('Content-Type')!r})"
                )

            local_name = f"FADA_{_month_full_name(month)[:3]}_{year}.pdf"
            pdf_path = save_dir / local_name
            pdf_path.write_bytes(pdf_response.content)
            print(f"Downloaded {title!r} -> {pdf_path}")
            return pdf_path

    raise FileNotFoundError(
        f"No press release titled {expected_title!r} found on "
        f"{PRESS_RELEASE_URL} (searched {max_pages} page(s))"
    )


def _parse_fada_header_date(cell_value) -> tuple[int, int]:
    """Parse FADA header cell like Apr'26 into (month, year)."""
    text = " ".join(str(cell_value or "").split())
    match = FADA_HEADER_DATE_RE.fullmatch(text)
    if not match:
        raise ValueError(f"Could not parse FADA header date from {cell_value!r}")
    month_raw, year_raw = match.group(1), match.group(2)
    year = int(year_raw)
    if year < 100:
        year += 2000
    for fmt in ("%B", "%b"):
        try:
            return datetime.strptime(month_raw.title(), fmt).month, year
        except ValueError:
            continue
    raise ValueError(f"Could not parse month from FADA header date {cell_value!r}")


def _header_date_from_table(table) -> str:
    """Return the current-period date from the table header's second column."""
    if not table or not table[0] or len(table[0]) < 2:
        raise ValueError("Table is missing a second-column header date")
    cell = table[0][1]
    if cell is None or not str(cell).strip():
        raise ValueError("Table header second column is empty (expected date like Apr'26)")
    return " ".join(str(cell).split())


def extract_table_by_title(pdf, table_titles, expected_date: str):
    """
    Extracts all tables whose page text matches one of table_titles.
    Captures the current-period date from each table's header column 2
    (e.g. Apr'26) and validates it against expected_date (e.g. Apr 2026).
    Returns {title: [rows]} dict.
    """
    results = {title: [] for title in table_titles}
    table_titles_lc = [t.lower() for t in table_titles]
    expected_my = _parse_input_date(expected_date)
    captured_dates: dict[str, str] = {}

    for page_num, page in enumerate(pdf.pages, 1):
        text = page.extract_text() or ""
        # Sometimes titles are vertical, so also try extracting all words with positions
        words = page.extract_words(x_tolerance=2, keep_blank_chars=True, use_text_flow=True)
        lines = [w['text'] for w in words if len(w['text']) > 3]
        # flatten into page text sequence
        joined_text = " ".join(lines).lower()

        for idx, title in enumerate(table_titles_lc):
            if title in text.lower() or title in joined_text:
                # Try find tables from this page
                tables = page.extract_tables()
                if not tables:
                    continue
                # For rough matching, try tables with at least 2 columns and 2 rows
                valid_tables = [t for t in tables if t and len(t) > 2 and len(t[0]) > 1]
                if valid_tables:
                    title_key = table_titles[idx]
                    for table in valid_tables:
                        header_date = _header_date_from_table(table)
                        header_my = _parse_fada_header_date(header_date)
                        if header_my != expected_my:
                            raise ValueError(
                                f"Date mismatch for {title_key!r}: "
                                f"header col2 is {header_date!r} but input date is "
                                f"{expected_date!r}"
                            )
                        captured_dates[title_key] = header_date
                    results[title_key].extend(valid_tables)

    missing = [t for t in table_titles if t not in captured_dates]
    if missing:
        raise ValueError(
            f"Could not capture header date (col 2) for tables: {missing}"
        )
    print(f"Validated FADA header dates against {expected_date!r}: {captured_dates}")
    return results

def extract_matching_data_with_gemini(
    api_key: str,
    input_text: str,
    item_names: list[str],
) -> str:
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-2.5-pro")
    prompt = f"""You are a precise data extraction engine.

            Task: Map the input text to the provided list of allowed item names and extract their associated numeric values.

            Allowed Item Names:
            {item_names}

            Rules for Extraction:
            1. Extract ONLY absolute numerical values associated with each item name found in the input text.
            2. Format values as pure numbers (e.g., 10,970 or 1,04,970). Keep indian stype commas format Remove all units, symbols, or currency text.
            3. If an item name from the allowed list is NOT mentioned in the input text, set its value to 0.
            4. Output MUST be a valid JSON object matching the key-value schema below. Do NOT wrap the response in markdown code blocks like ```json ... ``` unless requested, and do NOT include conversational preambles.

            Expected Output Format:
            {{
            "Item Name 1": "1020500",
            "Item Name 2": "0",
            "Item Name 3": "25050"
            }}

            Input Text:
            {input_text}"""
    response = model.generate_content(prompt)
    return response.text

def print_tables(tables):
    for title, table_list in tables.items():
        print(f"\n======= {title} =======\n")
        if not table_list:
            print(f"NO DATA FOUND for {title}")
            continue
        for table in table_list:
            for row in table:
                print(" | ".join(cell.strip() if cell else "" for cell in row))
            print("\n----------------------\n")

def main():
    month = "Jun"
    date = f"{month} 2026"
    scriptID = "S11"
    staging_kind = "StagingData_v1"
    granularity = "Monthly"
    property_name = "dataName"
    dataName = "this is picked form the value of tableTitles_dataNames"


    tableTitles_dataNames = {
        "Two-Wheeler OEM" : "Market Composition of Two Wheelers in India",
        "Three-Wheeler OEM" : "Market Composition of Three Wheelers in India",
        "Commercial Vehicle OEM" : "Market Composition of Commercial Vehicles in India",
        "Construction Equipment OEM" : "Market Composition of Construction Equipments in India",
        "PV OEM" : "Market Composition of Passenger Vehicles in India",
    }

    pdf_path = download_fada_retail_pdf(date)
    source_url = PRESS_RELEASE_URL

    with pdfplumber.open(pdf_path) as pdf:
        table_titles = list(tableTitles_dataNames.keys())
        all_tables = extract_table_by_title(pdf, table_titles, expected_date=date)
        print_tables(all_tables)
        api_key = get_gemini_api_key()
        for table_title, dataName in tableTitles_dataNames.items():
            tables = {table_title: all_tables.get(table_title, [])}
            item_names = fetch_item_names_from_csv_of_PublishedData_v1(dataName)
            print(f"\n--- {table_title} / {dataName} ---")
            print(item_names)
            gemini_output = extract_matching_data_with_gemini(api_key, tables, item_names)
            print(f"gemini_output for {table_title} ", gemini_output)
            parsed = parse_json_from_llm_output(gemini_output)
            print("parsed: ", parsed)
            update_Datastore(
                parsed, date, granularity, scriptID, source_url, dataName, staging_kind
            )

if __name__ == "__main__":
    main()