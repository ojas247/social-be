import csv
import io
import json
import re
import sys
import os
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, unquote
from dotenv import load_dotenv
import tempfile
from PyPDF2 import PdfReader

# Running this file directly puts .../viaPDF on sys.path, not the repo root — `utils` lives at repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.services.datastore_services import fetch_entities_by_property, update_entity
from app.utils.config import settings
from scripts.spyder.utils import get_last_date_of_month

# Helper 1: Download the latest PDF from the steel.gov.in summary table (first data row)
def download_latest_pdf(url, download_dir=None):
    response = requests.get(url, verify=False)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")

    # Drupal Views: real table is <table class="cols-3">; header is in <thead>, data in <tbody>.
    table = soup.select_one("table.cols-3")
    if not table:
        raise Exception("Could not find table.cols-3 with monthly PDF links.")

    tbody = table.find("tbody")
    if not tbody:
        raise Exception("Table has no tbody.")

    first_data_row = tbody.find("tr")
    if not first_data_row:
        raise Exception("No data rows in tbody (expected at least one PDF row).")

    # Documents column: class contains views-field-field-documents
    docs_cell = first_data_row.select_one("td.views-field-field-documents")
    if not docs_cell:
        tds = first_data_row.find_all("td", recursive=False)
        docs_cell = tds[-1] if tds else None
    if not docs_cell:
        raise Exception("Could not find documents cell in first data row.")

    first_pdf_link = None
    for a in docs_cell.find_all("a", href=True):
        href = (a["href"] or "").strip()
        if href.lower().split("?")[0].endswith(".pdf"):
            first_pdf_link = href
            break

    if not first_pdf_link:
        raise Exception("No PDF link found in the documents column of the first data row.")

    latest_pdf_url = urljoin(url, first_pdf_link)
    pdf_response = requests.get(latest_pdf_url, verify=False)
    if pdf_response.status_code != 200:
        raise Exception(f"Failed to download PDF: {latest_pdf_url}")
    filename = unquote(latest_pdf_url.split("/")[-1].split("?")[0])
    if not download_dir:
        fp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        fp.write(pdf_response.content)
        fp.close()
        return fp.name
    else:
        filepath = os.path.join(download_dir, filename)
        with open(filepath, "wb") as f:
            f.write(pdf_response.content)
        return filepath

# Helper 2: Read (extract text from) the PDF
def extract_text_from_pdf(pdf_path):
    reader = PdfReader(pdf_path)
    text = ""
    for page in reader.pages:
        text += page.extract_text() or ""
    return text


# def extract_chart_data_from_pdf_pages(
#     pdf_path: str,
#     api_key: str,
#     *,
#     model_name: str = "gemini-2.5-pro",
#     max_pages: int = 15,
#     dpi: float = 110.0,
#     max_image_side: int = 1280,
# ) -> str:
#     """
#     Rasterize PDF pages and ask a vision-capable Gemini model to read charts/graphs
#     that are not available as extractable text (PyPDF2 cannot OCR images).

#     Requires: pip install pymupdf pillow
#     """
#     try:
#         import fitz  # type: ignore[import-untyped]  # PyMuPDF
#         from PIL import Image
#         import google.generativeai as genai
#     except ImportError as e:
#         raise ImportError(
#             "Chart extraction needs pymupdf and pillow. Install with:\n"
#             "  pip install pymupdf pillow"
#         ) from e

#     genai.configure(api_key=api_key)
#     model = genai.GenerativeModel(model_name)

#     doc = fitz.open(pdf_path)
#     try:
#         n = min(doc.page_count, max_pages)
#         sections: list[str] = []
#         scale = dpi / 72.0
#         mat = fitz.Matrix(scale, scale)

#         for i in range(n):
#             page = doc.load_page(i)
#             pix = page.get_pixmap(matrix=mat, alpha=False)
#             img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
#             w, h = img.size
#             if max(w, h) > max_image_side:
#                 if w >= h:
#                     new_w, new_h = max_image_side, int(h * max_image_side / w)
#                 else:
#                     new_h, new_w = max_image_side, int(w * max_image_side / h)
#                 img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

#             prompt = (
#                 f"This is page {i + 1} of {doc.page_count} from an economic / steel industry PDF. "
#                 "If you see charts, graphs, or plots, transcribe the numeric data as accurately as you can: "
#                 "list series names, axis labels, units if visible, and values in markdown tables. "
#                 "If the page has only text or no quantitative chart, reply exactly: "
#                 "'No chart data on this page.'"
#             )
#             resp = model.generate_content([prompt, img])
#             if getattr(resp, "text", None):
#                 sections.append(f"### Page {i + 1}\n{resp.text}")
#             else:
#                 sections.append(f"### Page {i + 1}\n(no text response from vision model)")
#         return "\n\n".join(sections) if sections else "No pages processed for chart extraction."
#     finally:
#         doc.close()

def summarize_csv_structure_for_extraction(rows: list[list[str]]) -> str:
    """
    Describe CSV shape for the LLM: first row = headers (often Year/Month/periods),
    first column = item / indicator names, remaining cells = values per period.
    """
    if not rows:
        return "The reference CSV is empty."

    header = [c.strip() for c in rows[0]]
    data_rows = rows[1:] if len(rows) > 1 else []
    max_width = max((len(r) for r in rows), default=0)

    item_samples: list[str] = []
    for r in data_rows[:25]:
        if r and r[0].strip():
            item_samples.append(r[0].strip())

    period_labels = header[1:13] if len(header) > 1 else []
    extra_cols = max(0, len(header) - 13)

    lines = [
        "Reference data layout (from the CSV behind ReportUrl):",
        f"- Total parsed rows: {len(rows)}; widest row has {max_width} columns.",
        "- Treat the first row as column headers. Typically the first header cell labels the item/indicator column; "
        "remaining header cells label time periods (Year, Month, combined labels, etc.).",
        f"- First header row (up to 13 cells): {header[:13]!r}"
        + (f" ... (+{extra_cols} more columns)" if extra_cols else ""),
        f"- First column (item / indicator names): {len(data_rows)} data rows; examples: {item_samples[:12]!r}",
        "When reading the PDF, extract numbers/text that align with these item names and period columns. "
        "Output in a clear grid (markdown table or CSV-like lines): one row per item, columns matching the periods where values exist in the PDF.",
    ]
    return "\n".join(lines)


def fetch_item_names_from_TimeSeriesData(
    kind: str = "TimeSeriesData",
    property_name: str = "dataName",
    value: str = "Steel Production, Consumption, Import and Export in India",
) -> list[str]:
    """
    Load the TimeSeriesData entity, read ReportUrl (GCS or HTTPS CSV), infer structure, return summary for extraction.
    """
    entities = fetch_entities_by_property(
        kind=kind, property_name=property_name, value=value
    )
    if not entities:
        raise Exception(f"No entities of kind '{kind}' found for {property_name}={value!r}.")

    item_names = []
    for e in entities:
        item_names.append(e.get("item")) 
    item_names = list(set(item_names))
    print("item_names: ", item_names)
    return item_names


def extract_matching_data_with_gemini(
    api_key: str,
    pdf_text: str,
    item_names: list[str],
) -> str:
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-2.5-pro")
    prompt = (
        "You are extracting structured data from a government monthly economic report (PDF text).\n"
        f"The item names are: {item_names}\n"
        "Using the PDF text (and chart notes if any), fetch values for the item names. "
        "Return the values in a json format where key is the item name and value is the float value, you fetched for that item from the pdf text. Please note that the values should be pure digits, WITHOUT any UNITS or other text\n\n"
        "The json format should be like this: {{'item_name': 'value', 'item_name2': 'value2', ...}}\n\n"
        f"PDF text:\n{pdf_text}"
    )
    response = model.generate_content(prompt)
    return response.text


def parse_json_from_llm_output(text: str) -> dict:
    """
    Parse JSON from Gemini/LLM replies that may wrap output in ```json ... ``` fences
    or include extra prose before/after the object.
    """
    if not text or not str(text).strip():
        raise ValueError("Empty LLM output")

    raw = str(text).strip()

    # ```json\n{ ... }\n``` or ```\n{ ... }\n```
    fenced = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL | re.IGNORECASE)
    if fenced:
        raw = fenced.group(1).strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end > start:
            return json.loads(raw[start : end + 1])
        raise ValueError(f"Could not parse JSON from LLM output:\n{raw[:500]}")

# .env loader
def get_gemini_api_key(env_path=".env"):
    GEMINI_API_KEY = settings.LLM_API_KEY
    return GEMINI_API_KEY

def update_Datastore(parsed: dict):
    """
    Update the Datastore entity with the parsed data.
    """
    for item, value in parsed.items():
        entity = {
            "item": item,
            "value": value,
            "dataName": "Steel Production, Consumption, Import and Export in India",
            "publishedTS": datetime.now(), 
            "dateTime": get_last_date_of_month("May 2026")

        }
        update_entity(entity, kind="StagingData_v1")

# Main system orchestrator
def main():

     # 1. Load reference CSV shape from Datastore entity (ReportUrl -> GCS/HTTPS CSV)
    item_names = fetch_item_names_from_TimeSeriesData()

    # 2. Download the latest PDF from steel.gov.in monthly summary (top row)
    webpage_url = "https://steel.gov.in/monthly-summary"
    pdf_path = download_latest_pdf(webpage_url)

    # 3. Read the PDF (text layer)
    pdf_text = extract_text_from_pdf(pdf_path)
    # print("pdf_text (preview): ", pdf_text[:2000], "..." if len(pdf_text) > 2000 else "")

    # 4. Load Gemini API Key
    api_key = get_gemini_api_key()
    if not api_key:
        raise Exception("GEMINI_API_KEY not found in environment variables.")

   
    # 6. Run Gemini extraction guided by CSV structure + chart notes
    gemini_output = extract_matching_data_with_gemini(
        api_key, pdf_text, item_names
    )
    print("gemini_output: ", gemini_output)

    parsed = parse_json_from_llm_output(gemini_output)
    print("parsed: ", parsed)

    update_Datastore(parsed)
   

# If this file is run directly, run main()
if __name__ == "__main__":
    main()