"""
National Housing Bank — Monthly Credit Flow Data scraper.

Source page:
  https://www.nhb.org.in/monthly-credit-flow-data/?prophazecheck=1

Each tile in div.panel-body > li.list-group-item has a publish date, PDF link,
and file name. recency_row=1 selects the most recent report.
"""

import os
import re
import sys
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import pdfplumber
import requests
from bs4 import BeautifulSoup

from utils import extract_text_from_pdf, fetch_item_names_from_TimeSeriesData

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from app.services.datastore_services import update_Datastore

NHB_CREDIT_FLOW_URL = (
    "https://www.nhb.org.in/monthly-credit-flow-data/?prophazecheck=1"
)
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
FILE_NAME_RE = re.compile(r"File Name:-\s*(.+)", re.IGNORECASE)
CREDIT_FLOW_TITLE_RE = re.compile(
    r"Individual Home Loan Outstanding", re.IGNORECASE
)
# e.g. "as on June 30, 2026" / "as on 30 June 2026"
PDF_MONTH_RE = re.compile(
    r"as\s+on\s+(?P<month>[A-Za-z]+)\s+(?P<day>\d{1,2}),?\s+(?P<year>\d{4})",
    re.IGNORECASE,
)
# e.g. "30.06.2026"
PDF_NUMERIC_DATE_RE = re.compile(
    r"(?P<day>\d{1,2})\.(?P<month>\d{1,2})\.(?P<year>\d{4})"
)
FUZZY_THRESHOLD = 0.72


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _parse_tender_date(raw: str) -> datetime | None:
    """Parse tile publish date (DD/MM/YYYY)."""
    text = _normalize_text(raw)
    if not text:
        return None
    try:
        return datetime.strptime(text, "%d/%m/%Y")
    except ValueError:
        return None


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
    """Parse 'June 2026' / 'Jun 26' style input into (month, year)."""
    parts = date.strip().split()
    if len(parts) < 2 or not parts[-1].isdigit():
        raise ValueError(f"Expected date like 'June 2026', got {date!r}")
    year = int(parts[-1])
    if year < 100:
        year += 2000
    month = _parse_input_month(" ".join(parts[:-1]))
    return month, year


def _parse_file_name(li) -> str:
    for p in li.select("p"):
        match = FILE_NAME_RE.search(p.get_text(" ", strip=True))
        if match:
            return _normalize_text(match.group(1))
    href = li.select_one("a.download") or li.select_one('a[href$=".pdf"]')
    if href and href.get("href"):
        return Path(href["href"]).name
    return ""


def _parse_tile(li, base_url: str) -> dict | None:
    """Extract one credit-flow tile from a li.list-group-item."""
    pdf_a = li.select_one('a.download[href$=".pdf"]') or li.select_one(
        'a[href$=".pdf"]'
    )
    if not pdf_a or not pdf_a.get("href"):
        return None

    title_a = li.select_one("a.download") or pdf_a
    title = _normalize_text(title_a.get_text(" ", strip=True))
    if title and not CREDIT_FLOW_TITLE_RE.search(title):
        return None

    date_span = li.select_one("span.tender-date")
    publish_date_raw = date_span.get_text(strip=True) if date_span else ""
    pdf_url = urljoin(base_url, pdf_a["href"])

    return {
        "publish_date": publish_date_raw,
        "publish_date_dt": _parse_tender_date(publish_date_raw),
        "title": title,
        "pdf_url": pdf_url,
        "file_name": _parse_file_name(li),
    }


def fetch_credit_flow_tiles(url: str = NHB_CREDIT_FLOW_URL) -> list[dict]:
    """
    Scrape Monthly Credit Flow tiles from div.panel-body.

    Returns tiles sorted most-recent-first (recency_row=1 => index 0).
    """
    response = requests.get(url, headers=REQUEST_HEADERS, timeout=60)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")

    tiles: list[dict] = []
    seen_urls: set[str] = set()

    for panel in soup.select("div.panel-body"):
        for li in panel.select("li.list-group-item"):
            tile = _parse_tile(li, url)
            if not tile or tile["pdf_url"] in seen_urls:
                continue
            seen_urls.add(tile["pdf_url"])
            tiles.append(tile)

    if not tiles:
        raise RuntimeError(
            f"No Monthly Credit Flow PDF tiles found on {url}"
        )

    # Page lists oldest-first; recency_row=1 should be the latest report.
    tiles.sort(
        key=lambda t: t["publish_date_dt"] or datetime.min,
        reverse=True,
    )
    return tiles


def download_nhb_pdf(
    tile: dict,
    download_dir: Path | None = None,
) -> Path:
    """Download the tile PDF and return the local path."""
    save_dir = download_dir or DOWNLOADS_DIR
    save_dir.mkdir(parents=True, exist_ok=True)

    file_name = tile.get("file_name") or Path(tile["pdf_url"]).name
    pdf_path = save_dir / file_name

    response = requests.get(tile["pdf_url"], headers=REQUEST_HEADERS, timeout=120)
    response.raise_for_status()
    pdf_path.write_bytes(response.content)
    print(f"Downloaded {tile['title']!r} -> {pdf_path}")
    return pdf_path


def fetch_nhb_report(
    recency_row: int = 1,
    url: str = NHB_CREDIT_FLOW_URL,
    download_dir: Path | None = None,
) -> dict:
    """
    Fetch one NHB Monthly Credit Flow PDF by recency.

    recency_row=1 -> most recent report (latest publish date on the page).
    recency_row=2 -> second most recent, and so on.
    """
    if recency_row < 1:
        raise ValueError("recency_row must be >= 1")

    tiles = fetch_credit_flow_tiles(url)
    if recency_row > len(tiles):
        raise IndexError(
            f"recency_row={recency_row} out of range; only {len(tiles)} tiles found"
        )

    tile = tiles[recency_row - 1]
    pdf_path = download_nhb_pdf(tile, download_dir=download_dir)
    pdf_text = extract_text_from_pdf(str(pdf_path))

    return {
        **tile,
        "recency_row": recency_row,
        "pdf_path": pdf_path,
        "pdf_text": pdf_text,
    }


# TEMP: delete once scrape path is verified.
def fetch_nhb_report_from_local(
    file_name: str = "Website-Publication-format-June26.pdf",
    download_dir: Path | None = None,
) -> dict:
    """
    TEMP helper: skip website scrape and read a PDF already in downloads/.
    """
    save_dir = download_dir or DOWNLOADS_DIR
    pdf_path = save_dir / file_name
    if not pdf_path.is_file():
        raise FileNotFoundError(f"Local NHB PDF not found: {pdf_path}")

    pdf_text = extract_text_from_pdf(str(pdf_path))
    return {
        "publish_date": "",
        "publish_date_dt": None,
        "title": file_name,
        "pdf_url": str(pdf_path),
        "file_name": file_name,
        "recency_row": None,
        "pdf_path": pdf_path,
        "pdf_text": pdf_text,
    }


def extract_pdf_report_date(pdf_path: Path, pdf_text: str = "") -> tuple[int, int]:
    """
    Parse the report month/year from PDF text.
    Prefers 'as on June 30, 2026', falls back to '30.06.2026'.
    """
    text = pdf_text or extract_text_from_pdf(str(pdf_path))
    match = PDF_MONTH_RE.search(text)
    if match:
        month = _parse_input_month(match.group("month"))
        year = int(match.group("year"))
        return month, year

    match = PDF_NUMERIC_DATE_RE.search(text)
    if match:
        return int(match.group("month")), int(match.group("year"))

    raise ValueError(f"Could not find report date in PDF: {pdf_path}")


def validate_month_against_pdf(
    month: str,
    date: str,
    pdf_path: Path,
    pdf_text: str = "",
) -> tuple[int, int]:
    """Ensure main() month/date match the month/year stated in the PDF."""
    input_month = _parse_input_month(month)
    date_month, date_year = _parse_input_date(date)
    if input_month != date_month:
        raise ValueError(
            f"Month mismatch between month={month!r} and date={date!r}"
        )

    pdf_month, pdf_year = extract_pdf_report_date(pdf_path, pdf_text=pdf_text)
    if (pdf_month, pdf_year) != (date_month, date_year):
        pdf_label = datetime(pdf_year, pdf_month, 1).strftime("%B %Y")
        raise ValueError(
            f"Input date {date!r} does not match PDF report month "
            f"({pdf_label}) in {pdf_path.name}"
        )
    return pdf_month, pdf_year


def _to_number(value) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(round(value))
    text = str(value).strip().replace(",", "").replace("₹", "")
    if not text or text in {"-", "—", "NA", "N/A"}:
        return None
    try:
        num = float(text)
    except ValueError:
        return None
    return int(round(num))


def parse_nhb_table(pdf_path: Path) -> list[dict]:
    """
    Parse PLI Category table from the NHB PDF.
    Returns rows like {"Item": "Housing Finance Companies", "Value": 734538}.
    Skips header / Grand Total rows.
    """
    rows: list[dict] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                for raw in table:
                    cells = [(_normalize_text(c) if c else "") for c in raw]
                    if len(cells) < 2:
                        continue

                    # Typical shape: [Sl, Category, Value]
                    if len(cells) >= 3:
                        item, value_raw = cells[1], cells[2]
                    else:
                        item, value_raw = cells[0], cells[1]

                    item_l = item.lower()
                    if not item or item_l.startswith("primary lending"):
                        continue
                    if "outstanding" in item_l or item_l in {"sl", "s.l", "s.no"}:
                        continue
                    if "grand total" in item_l or item_l == "total":
                        continue

                    value = _to_number(value_raw)
                    if value is None:
                        continue
                    rows.append({"Item": item, "Value": value})

    if not rows:
        raise RuntimeError(f"No PLI category rows found in {pdf_path}")
    return rows


def get_match_ratio(s1, s2) -> float:
    return SequenceMatcher(None, str(s1), str(s2)).ratio()


def match_master_to_pdf_rows(
    pdf_rows: list[dict],
    master_item_names: list[str],
) -> pd.DataFrame:
    """Keep only PDF rows that fuzzy-match master_item_names."""
    candidates = [
        row for row in pdf_rows if row.get("Item") and row.get("Value") is not None
    ]
    used_indices: set[int] = set()
    matched = []

    for master in master_item_names:
        best_idx = None
        best_score = 0.0
        for i, row in enumerate(candidates):
            if i in used_indices:
                continue
            score = get_match_ratio(master.lower(), str(row["Item"]).lower())
            if score > best_score:
                best_score = score
                best_idx = i

        if best_idx is None or best_score < FUZZY_THRESHOLD:
            continue

        used_indices.add(best_idx)
        matched.append({
            "Item": master,
            "Value": candidates[best_idx]["Value"],
            "PDF_Item": candidates[best_idx]["Item"],
            "MatchScore": round(best_score, 3),
        })

    return pd.DataFrame(matched)


def main():
    month = "May"
    date = f"{month} 2026"
    scriptID = "S13"
    staging_kind = "StagingData_v1"
    granularity = "Monthly"
    ts_kind = "TimeSeriesData"
    property_name = "dataName"
    dataName = "Category wise Outstanding Individual Housing Loans"
    units = "INR Cr"
    recency_row = 1

    # TEMP: use local downloads PDF; switch back to fetch_nhb_report later.
    report = fetch_nhb_report_from_local(
        file_name="Website-Publication-format-May26-1.pdf"
    )
    # report = fetch_nhb_report(recency_row=recency_row)

    validate_month_against_pdf(
        month=month,
        date=date,
        pdf_path=report["pdf_path"],
        pdf_text=report["pdf_text"],
    )

    master_item_names = fetch_item_names_from_TimeSeriesData(
        ts_kind, property_name, dataName
    )
    pdf_rows = parse_nhb_table(report["pdf_path"])
    print("PDF rows:")
    for row in pdf_rows:
        print(f"  {row['Item']}: {row['Value']}")

    df = match_master_to_pdf_rows(pdf_rows, master_item_names)
    print("\nMatched rows:")
    print(df.to_string(index=False) if not df.empty else "(none)")

    missing = [m for m in master_item_names if m not in set(df.get("Item", []))]
    if missing:
        print(f"\nUnmatched master entries ({len(missing)}):")
        for m in missing:
            print(f"  - {m}")

    if df.empty:
        raise RuntimeError("No master items matched PDF table rows")

    parsed = {row["Item"]: row["Value"] for row in df.to_dict(orient="records")}
    source = report.get("pdf_url") or str(report["pdf_path"])
    update_Datastore(
        parsed,
        date,
        granularity,
        scriptID,
        source,
        dataName,
        staging_kind,
    )

    print(f"\nUpdated Datastore ({staging_kind}) for {dataName!r} @ {date}")
    print(f"Items written: {len(parsed)}")


if __name__ == "__main__":
    main()
