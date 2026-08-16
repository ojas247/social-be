"""
Load cached vector embeddings and extract AR_schema/QR_schema line items via
RAG + LLM, for a specific reporting period.

Prompts for:
  1) folder name under scripts/company
  2) PDF file name (used to locate {pdf_stem}.json embeddings)
  3) report type: AR (annual) or QR (quarterly)
  4) target reporting period (e.g. "Q2 FY25") + how it's labeled in the
     report itself (e.g. "3 months ended September 30, 2024") + the period
     end date used for the Date column
  5) (optional) comparative/prior period label(s) to explicitly exclude

Requires:
  - scripts/company/{folder}/{pdf_stem}.json   (from co_embedding.py)
  - scripts/company/{folder}/AR_schema.json or QR_schema.json

Writes:
  - scripts/company/{folder}/{pdf_stem}_extracted.json

Improvements over the previous version:
  - PERIOD DISAMBIGUATION (this update): financial statements almost always
    show the current period next to one or more comparative periods in the
    same table (e.g. current quarter vs. prior quarter vs. same quarter
    last year). Retrieval alone can't fix this — the right table gets
    retrieved, but nothing tells the LLM which *column* to read. This
    version:
      * Asks explicitly for the target period, its as-printed label, and
        (optionally) prior-period labels to avoid.
      * Includes the target period in the retrieval query so period-labeled
        chunks rank higher.
      * Instructs the LLM to report which period label each value was read
        under, not just the value.
      * Validates the reported period against the target period (and
        against the excluded/comparative labels) before accepting a value;
        anything that doesn't match the target period is rejected, not
        silently kept.
  - Query embeddings are batched, with retry/backoff.
  - Retrieval scoring blends cosine similarity with a section match bonus
    and a keyword-overlap bonus.
  - Top hits are expanded with their immediate neighbor chunks.
  - Extraction runs per Category rather than one giant call.
  - Extracted numeric values are grounding-checked against the retrieved
    context text before being accepted.
  - "Not found" is represented as None (not 0).
"""

import importlib.util
import json
import math
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import google.generativeai as genai

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from app.services.datastore_services import update_entity
from app.utils.dates import get_last_date_of_month

# Load scripts/spyder/utils.py by path (sibling package, not on PYTHONPATH).
_SPYDER_UTILS = Path(__file__).resolve().parent.parent / "spyder" / "utils.py"
_spec = importlib.util.spec_from_file_location("spyder_utils", _SPYDER_UTILS)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load utils from {_SPYDER_UTILS}")
_spyder_utils = importlib.util.module_from_spec(_spec)
sys.modules["spyder_utils"] = _spyder_utils
_spec.loader.exec_module(_spyder_utils)
get_gemini_api_key = _spyder_utils.get_gemini_api_key
parse_json_from_llm_output = _spyder_utils.parse_json_from_llm_output

COMPANY_ROOT = Path(__file__).resolve().parent
SCHEMA_BY_REPORT_TYPE = {
    "AR": "AR_schema.json",
    "QR": "QR_schema.json",
}
EMBEDDING_MODEL = "models/gemini-embedding-001"
GENERATION_MODEL = "gemini-2.5-pro"
TOP_K_CHUNKS = 5
NEIGHBOR_WINDOW = 1  # include chunk_idx +/- this many neighbors
SECTION_MATCH_BONUS = 0.05
KEYWORD_MATCH_BONUS = 0.03
PERIOD_MATCH_BONUS = 0.05
MAX_RETRIES = 4
INITIAL_BACKOFF = 2.0
_H_LEVEL_RE = re.compile(r"^H\d+$")
_NUMBER_RE = re.compile(r"-?\d[\d,]*\.?\d*")


def prompt_run_inputs() -> tuple[str, str, Path, Path, Path, Path, Path, dict]:
    """
    Ask for folder, PDF name, report type, and the target reporting period.
    Returns (folder_name, report_type, company_dir, pdf_path, vectors_path,
              schema_path, output_path, period_info).

    period_info = {
        "target_period": "Q2 FY25",                              # short label, used for the Date/period columns
        "as_printed": "3 months ended September 30, 2024",       # how it appears in the actual document
        "period_end_date": "30-Sep-2024",                        # used for the Date column / datastore dateTime
        "exclude_labels": ["3 months ended September 30, 2023"], # comparative periods to actively reject
    }
    """
    folder_name = input("Folder name (under scripts/company): ").strip().strip('"').strip("'")
    pdf_name = input("PDF file name: ").strip().strip('"').strip("'")
    report_type = input("Report type (AR/QR): ").strip().strip('"').strip("'").upper()
    if not folder_name:
        raise ValueError("Folder name is required")
    if not pdf_name:
        raise ValueError("PDF file name is required")
    if not pdf_name.lower().endswith(".pdf"):
        pdf_name = f"{pdf_name}.pdf"
    if report_type not in SCHEMA_BY_REPORT_TYPE:
        raise ValueError("Report type must be AR (annual) or QR (quarterly)")

    print(
        "\nReports usually show the current period next to comparative periods "
        "(e.g. prior quarter, same quarter last year) in the same table.\n"
        "Tell me exactly which period to extract so the right column gets picked."
    )
    target_period = input("Target reporting period (e.g. 'Q2 FY25'): ").strip().strip('"').strip("'")
    if not target_period:
        raise ValueError("Target reporting period is required")

    as_printed = input(
        "How is this period labeled in the report itself? "
        "(e.g. '3 months ended September 30, 2024' — press Enter to reuse the label above): "
    ).strip().strip('"').strip("'")
    if not as_printed:
        as_printed = target_period

    period_end_date = input(
        "Period end date for this report (e.g. '30-Sep-2024', used for the Date column): "
    ).strip().strip('"').strip("'")
    if not period_end_date:
        raise ValueError("Period end date is required")

    exclude_raw = input(
        "Comparative/prior period label(s) to EXCLUDE, comma-separated "
        "(optional, e.g. '3 months ended September 30, 2023, 3 months ended June 30, 2024'): "
    ).strip()
    exclude_labels = [x.strip() for x in exclude_raw.split(",") if x.strip()]

    period_info = {
        "target_period": target_period,
        "as_printed": as_printed,
        "period_end_date": period_end_date,
        "exclude_labels": exclude_labels,
    }

    schema_filename = SCHEMA_BY_REPORT_TYPE[report_type]
    company_dir = COMPANY_ROOT / folder_name
    stem = Path(pdf_name).stem
    pdf_path = company_dir / pdf_name
    vectors_path = company_dir / f"{stem}.json"
    schema_path = company_dir / schema_filename
    output_path = company_dir / f"{stem}_extracted.json"

    if not company_dir.is_dir():
        raise FileNotFoundError(f"Folder not found: {company_dir}")
    if not vectors_path.is_file():
        raise FileNotFoundError(
            f"Embeddings JSON not found: {vectors_path}\n"
            f"Run co_embedding.py first for this PDF."
        )
    if not schema_path.is_file():
        raise FileNotFoundError(f"Schema not found: {schema_path}")

    return (
        folder_name,
        report_type,
        company_dir,
        pdf_path,
        vectors_path,
        schema_path,
        output_path,
        period_info,
    )


def load_ar_schema(schema_path: Path) -> dict:
    with open(schema_path, encoding="utf-8") as f:
        schema = json.load(f)
    if not schema.get("line_items"):
        raise ValueError(f"No line_items found in {schema_path}")
    return schema


def load_vectors(vectors_path: Path) -> list[dict]:
    with open(vectors_path, encoding="utf-8") as f:
        vectors = json.load(f)
    if not vectors:
        raise RuntimeError(f"Saved embeddings file is empty: {vectors_path}")
    return vectors


def cosine_similarity(vec_a, vec_b) -> float:
    """Cosine similarity between two embedding vectors."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


# --------------------------------------------------------------------------
# Retry helpers
# --------------------------------------------------------------------------

def _with_retry(fn, *args, max_retries: int = MAX_RETRIES, **kwargs):
    """Call fn(*args, **kwargs) with exponential backoff. Raises on final failure."""
    backoff = INITIAL_BACKOFF
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_err = e
            print(f"  Attempt {attempt}/{max_retries} failed: {e}")
            if attempt < max_retries:
                time.sleep(backoff)
                backoff *= 2
    raise RuntimeError(f"Call failed after {max_retries} attempts: {last_err}")


# --------------------------------------------------------------------------
# Period matching helpers
#
# NOTE: comparative period labels in financial statements share almost all
# of their wording — "3 months ended September 30, 2024" vs "3 months ended
# June 30, 2024" differ by exactly one word. Fuzzy word-overlap matching
# treats those as ~80% identical, which is *worse than useless* here — it
# either rejects the correct period or accepts the wrong one. The one thing
# that reliably differs between comparative columns is the actual date, so
# matching is date-based first, with duration ("3 months" vs "6 months" vs
# "12 months") as a tiebreaker for same-end-date YTD columns. Only when a
# date can't be parsed from either side do we fall back to strict (not
# fuzzy) substring matching.
# --------------------------------------------------------------------------

_MONTH_MAP = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

_DURATION_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}


def _normalize_period(text: str) -> str:
    """Lowercase, strip punctuation/spaces. Used only for exact/substring
    fallback comparisons, not for fuzzy scoring."""
    if not text:
        return ""
    text = text.lower()
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text


def _extract_date(text: str):
    """Best-effort extraction of a (year, month, day) tuple from a period
    label or date string. Returns None if no recognizable date is found."""
    if not text:
        return None
    t = text.strip()

    # "September 30, 2024" / "Sep 30 2024" / "September 30th, 2024"
    m = re.search(r"([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", t)
    if m:
        mon = _MONTH_MAP.get(m.group(1).lower())
        if mon:
            return (int(m.group(3)), mon, int(m.group(2)))

    # "30 September 2024" / "30-Sep-2024" / "30/Sep/2024"
    m = re.search(r"(\d{1,2})[-\s/]([A-Za-z]{3,9})\.?[-\s/,]+(\d{4})", t)
    if m:
        mon = _MONTH_MAP.get(m.group(2).lower())
        if mon:
            return (int(m.group(3)), mon, int(m.group(1)))

    # Numeric "30/09/2024" or "09/30/2024" — assume month/day/year (US-style)
    # when the first number could plausibly be a month; this is a fallback
    # only used when no month name is present.
    m = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", t)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if a <= 12 and b <= 31:
            return (y, a, b)

    return None


def _extract_duration_months(text: str):
    """Best-effort extraction of a period duration in months from a label
    (e.g. 3 for a quarter, 6 for a half-year, 12 for a full year). Returns
    None if no duration phrase is found."""
    if not text:
        return None
    t = text.lower()

    m = re.search(r"(\d+)\s*-?\s*months?", t)
    if m:
        return int(m.group(1))
    for word, num in _DURATION_WORDS.items():
        if re.search(rf"\b{word}\b\s*-?\s*months?", t):
            return num
    if re.search(r"\bquarter\b", t):
        return 3
    if re.search(r"\bhalf[-\s]?year\b", t):
        return 6
    if re.search(r"\b(year|annual|fy)\b", t):
        return 12
    return None


def _period_matches_target(returned_period: str, period_info: dict) -> bool:
    """True if returned_period is the target period and NOT one of the
    explicitly excluded comparative periods.

    Primary check: parsed end-date equality (+ duration as a tiebreaker for
    same-end-date columns, e.g. quarter vs. year-to-date). Falls back to a
    strict (non-fuzzy) substring match only when a date can't be parsed on
    either side.
    """
    if not returned_period:
        return False

    returned_date = _extract_date(returned_period)
    returned_duration = _extract_duration_months(returned_period)

    target_date = _extract_date(period_info.get("period_end_date", "")) or _extract_date(
        period_info.get("as_printed", "")
    )
    target_duration = _extract_duration_months(
        period_info.get("as_printed", "")
    ) or _extract_duration_months(period_info.get("target_period", ""))

    # --- Exclusion check (same date-based logic) ---
    for excl in period_info.get("exclude_labels", []):
        excl_date = _extract_date(excl)
        excl_duration = _extract_duration_months(excl)
        if excl_date and returned_date and excl_date == returned_date:
            if (
                excl_duration is None
                or returned_duration is None
                or excl_duration == returned_duration
            ):
                return False
        elif _normalize_period(excl) and _normalize_period(excl) == _normalize_period(returned_period):
            return False

    # --- Primary: date match ---
    if target_date and returned_date:
        if target_date != returned_date:
            return False
        if (
            target_duration is not None
            and returned_duration is not None
            and target_duration != returned_duration
        ):
            return False
        return True

    # --- Fallback: strict substring match (no date parseable either side) ---
    norm_returned = _normalize_period(returned_period)
    for cand in [period_info["target_period"], period_info["as_printed"]]:
        norm_cand = _normalize_period(cand)
        if norm_cand and (norm_cand == norm_returned or norm_cand in norm_returned or norm_returned in norm_cand):
            return True
    return False


# --------------------------------------------------------------------------
# Query embedding (batched)
# --------------------------------------------------------------------------

def _line_item_label(item: dict) -> str:
    parts = [
        item.get("Category") or "",
        item.get("H1") or "",
        item.get("H2") or "",
        item.get("Item") or "",
    ]
    return " > ".join(p for p in parts if p and p != "--")


def _build_query_text(item: dict, period_info: dict) -> str:
    label = _line_item_label(item)
    units = item.get("units") or ""
    return (
        f"Find the absolute numeric value for '{item.get('Item')}' "
        f"({label}; units: {units}) for the period {period_info['as_printed']} "
        f"({period_info['target_period']}) from the company report."
    )


def embed_queries_batch(api_key: str, queries: list[str]) -> list[list[float]]:
    """Embed all line-item queries in a single batched call, with retry."""
    genai.configure(api_key=api_key, transport="rest")

    def _call():
        response = genai.embed_content(
            model=EMBEDDING_MODEL,
            content=queries,
            task_type="retrieval_query",
        )
        embedding = response["embedding"]
        # Batch call returns list[list[float]] when content is a list.
        if embedding and isinstance(embedding[0], (int, float)):
            return [embedding]
        return embedding

    return _with_retry(_call)


# --------------------------------------------------------------------------
# Retrieval: section + keyword + period boosted, with neighbor expansion
# --------------------------------------------------------------------------

def _keyword_overlap_bonus(item: dict, chunk_text: str) -> float:
    """Small score bonus if the Item name's significant words literally
    appear in the chunk text — helps break ties between near-synonym
    financial terms that sit close together in embedding space."""
    item_name = (item.get("Item") or "").lower()
    words = [w for w in re.findall(r"[a-z]+", item_name) if len(w) > 3]
    if not words:
        return 0.0
    text_lower = chunk_text.lower()
    hits = sum(1 for w in words if w in text_lower)
    return KEYWORD_MATCH_BONUS * (hits / len(words))


def _section_match_bonus(item: dict, chunk_section: str) -> float:
    category = (item.get("Category") or "").strip().lower()
    section = (chunk_section or "").strip().lower()
    if category and section and (category in section or section in category):
        return SECTION_MATCH_BONUS
    return 0.0


def _period_mention_bonus(chunk_text: str, period_info: dict) -> float:
    """Small bonus if the chunk text literally mentions the target period's
    end date — favors chunks that show the right period's column, without
    penalizing chunks that don't mention any date at all. This is a soft
    ranking nudge only; the hard accept/reject check is _period_matches_target."""
    target_date = _extract_date(period_info.get("period_end_date", "")) or _extract_date(
        period_info.get("as_printed", "")
    )
    if not target_date:
        # No parseable target date — fall back to a plain substring check.
        norm_target = _normalize_period(period_info["as_printed"])
        norm_text = _normalize_period(chunk_text)
        return PERIOD_MATCH_BONUS if norm_target and norm_target in norm_text else 0.0

    # Scan the chunk text for any date and see if one matches the target.
    for m in re.finditer(r"[A-Za-z]{3,9}\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}", chunk_text):
        if _extract_date(m.group(0)) == target_date:
            return PERIOD_MATCH_BONUS
    for m in re.finditer(r"\d{1,2}[-\s/][A-Za-z]{3,9}\.?[-\s/,]+\d{4}", chunk_text):
        if _extract_date(m.group(0)) == target_date:
            return PERIOD_MATCH_BONUS
    return 0.0


def retrieve_nearest_chunks(
    item: dict,
    vectors: list[dict],
    query_embedding: list[float],
    period_info: dict,
    top_k: int = TOP_K_CHUNKS,
) -> list[dict]:
    """
    Return top_k chunks ranked by cosine similarity blended with a section
    match bonus, a keyword overlap bonus, and a period-mention bonus.
    """
    if not vectors:
        return []
    scored = []
    for v in vectors:
        cos = cosine_similarity(query_embedding, v["embedding"])
        bonus = (
            _section_match_bonus(item, v.get("section", ""))
            + _keyword_overlap_bonus(item, v.get("text", ""))
            + _period_mention_bonus(v.get("text", ""), period_info)
        )
        scored.append({**v, "cosine": cos, "score": cos + bonus})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[: max(1, top_k)]


def _expand_with_neighbors(
    hits: list[dict], vectors_by_idx: dict[int, dict], window: int = NEIGHBOR_WINDOW
) -> list[dict]:
    """Add immediate neighbor chunks for each hit (tables/rows often
    straddle chunk boundaries), deduped, keeping the original hit's score
    on neighbors it pulled in (marked as context-only)."""
    expanded: dict[int, dict] = {}
    for hit in hits:
        idx = hit["chunk_idx"]
        expanded[idx] = hit
        for offset in range(-window, window + 1):
            n_idx = idx + offset
            if offset == 0 or n_idx not in vectors_by_idx:
                continue
            if n_idx not in expanded:
                neighbor = dict(vectors_by_idx[n_idx])
                neighbor["score"] = hit["score"] * 0.9  # slightly deprioritized
                neighbor["neighbor_of"] = idx
                expanded[n_idx] = neighbor
    return list(expanded.values())


def retrieve_chunks_for_schema_items(
    api_key: str,
    vectors: list[dict],
    line_items: list[dict],
    period_info: dict,
    top_k: int = TOP_K_CHUNKS,
) -> dict[str, list[dict]]:
    """
    For each schema line item, embed a period-aware query (batched) and take
    top_k nearest chunks (section+keyword+period boosted, neighbor-expanded).

    Returns {Category: [unique chunks sorted by score desc]} so extraction
    can be run per category instead of one giant call.
    """
    vectors_by_idx = {v["chunk_idx"]: v for v in vectors}
    queries = [_build_query_text(item, period_info) for item in line_items]
    print(f"Embedding {len(queries)} line-item queries in one batch (period-aware)...")
    query_embeddings = embed_queries_batch(api_key, queries)

    by_category: dict[str, dict[int, dict]] = {}
    for item, query_embedding in zip(line_items, query_embeddings):
        category = item.get("Category") or "Uncategorized"
        label = _line_item_label(item)
        nearest = retrieve_nearest_chunks(item, vectors, query_embedding, period_info, top_k=top_k)
        nearest = _expand_with_neighbors(nearest, vectors_by_idx)
        print(f"Nearest chunks for {label!r}:")
        for hit in sorted(nearest, key=lambda x: x["score"], reverse=True):
            tag = f" (neighbor of {hit['neighbor_of']})" if "neighbor_of" in hit else ""
            print(f"  chunk={hit['chunk_idx']} score={hit['score']:.4f}{tag}")

        bucket = by_category.setdefault(category, {})
        for hit in nearest:
            prev = bucket.get(hit["chunk_idx"])
            if prev is None or hit["score"] > prev["score"]:
                bucket[hit["chunk_idx"]] = hit
        time.sleep(0.1)

    return {
        category: sorted(chunks.values(), key=lambda x: x["score"], reverse=True)
        for category, chunks in by_category.items()
    }


# --------------------------------------------------------------------------
# Extraction: per-category calls + period validation + grounding check
# --------------------------------------------------------------------------

def _format_context(chunks: list[dict]) -> str:
    return "\n\n---\n\n".join(
        f"[chunk {c['chunk_idx']} | page {c.get('page_start', '?')}-{c.get('page_end', '?')} "
        f"| section: {c.get('section', 'unknown')} | score={c.get('score', 0):.4f}]\n{c['text']}"
        for c in chunks
    )


def _extract_category_with_gemini(
    api_key: str, context: str, category_items: list[dict], period_info: dict
) -> dict:
    genai.configure(api_key=api_key, transport="rest")
    model = genai.GenerativeModel(GENERATION_MODEL)
    item_names = [item["Item"] for item in category_items]
    output_schema = {
        name: {"value": "number_or_null", "page": "page_number_or_null", "period": "period_label_or_null"}
        for name in item_names
    }

    exclude_note = ""
    if period_info.get("exclude_labels"):
        exclude_list = "\n".join(f"  - {p}" for p in period_info["exclude_labels"])
        exclude_note = (
            "\nThe following are COMPARATIVE/PRIOR periods that will also appear in these "
            "tables. Do NOT extract values from these columns even if they are easier to find:\n"
            f"{exclude_list}\n"
        )

    prompt = f"""You will extract only the required values as strict JSON.

Task: Extract absolute numeric values for these report line items, but ONLY for the
following reporting period — nothing else:

  TARGET PERIOD: {period_info['target_period']}
  AS LABELED IN THE DOCUMENT: {period_info['as_printed']}
{exclude_note}
Financial statements typically show the target period side-by-side with one or more
comparative periods (prior quarter, same quarter last year, year-to-date, etc.) in the
same table or row. You must identify which COLUMN or LABEL in the source text corresponds
to the target period above, and extract the value from that column only. If you cannot
confidently tell which column belongs to the target period, return null rather than guessing.

Use the Item name as the JSON key. Hierarchy metadata is provided for disambiguation:
{json.dumps(category_items, indent=2)}

Rules:
1. Keys MUST exactly match the Item names listed above.
2. "value" must be a pure number (no currency symbols, no commas) or null if not found for
   the TARGET PERIOD specifically.
3. "page" is the page number the value was read from (from the chunk header), or null.
4. "period" is the exact period label/column header text (as it appears in the source) that
   this value was read under. This is required whenever value is not null — it is used to
   verify you picked the right column, so be precise and copy it as printed.
5. Do NOT guess or infer a value that is not explicitly present in the source chunks below.
6. Do NOT extract a value from a comparative/prior period column, even if it's the only
   number you can find — return null instead.

Output JSON format:
{json.dumps(output_schema, indent=2)}

Source Data (nearest retrieved chunks only, for this category):
{context}
"""

    def _call():
        response = model.generate_content(prompt)
        return parse_json_from_llm_output(response.text)

    return _with_retry(_call)


def _is_grounded(value, context: str) -> bool:
    """Check the numeric value literally appears in the retrieved context
    (allowing for comma formatting) before we trust it."""
    if value is None:
        return True  # nothing to ground
    try:
        num_str = str(value).replace(",", "").strip()
        float(num_str)  # validate it's actually numeric
    except (ValueError, TypeError):
        return False

    context_numbers = {n.replace(",", "") for n in _NUMBER_RE.findall(context)}
    candidates = {num_str, num_str.rstrip("0").rstrip(".")}
    return bool(candidates & context_numbers)


def extract_values_with_gemini(
    api_key: str,
    chunks_by_category: dict[str, list[dict]],
    line_items: list[dict],
    period_info: dict,
) -> dict:
    """
    Run one extraction call per Category, then for each returned value:
      1. Reject it if it's not grounded in the retrieved context text.
      2. Reject it if the LLM's reported "period" label doesn't match the
         target period (or matches an excluded comparative period).
    Only values passing both checks are kept.
    """
    items_by_category: dict[str, list[dict]] = {}
    for item in line_items:
        category = item.get("Category") or "Uncategorized"
        items_by_category.setdefault(category, []).append(item)

    all_extracted: dict[str, dict] = {}
    for category, category_items in items_by_category.items():
        chunks = chunks_by_category.get(category, [])
        if not chunks:
            print(f"WARNING: no retrieved chunks for category {category!r}; skipping ({len(category_items)} items)")
            for item in category_items:
                all_extracted[item["Item"]] = {"value": None, "page": None, "period": None}
            continue

        context = _format_context(chunks)
        print(f"Extracting {len(category_items)} item(s) for category {category!r} "
              f"from {len(chunks)} chunk(s)...")
        result = _extract_category_with_gemini(api_key, context, category_items, period_info)

        for item in category_items:
            name = item["Item"]
            entry = result.get(name) or {}
            value = entry.get("value") if isinstance(entry, dict) else entry
            page = entry.get("page") if isinstance(entry, dict) else None
            returned_period = entry.get("period") if isinstance(entry, dict) else None

            if value is not None and not _is_grounded(value, context):
                print(f"  REJECTED (not grounded in source text): {name!r} = {value!r}")
                value = None

            if value is not None and not _period_matches_target(returned_period, period_info):
                print(
                    f"  REJECTED (wrong period — got {returned_period!r}, "
                    f"want {period_info['target_period']!r}): {name!r} = {value!r}"
                )
                value = None

            all_extracted[name] = {"value": value, "page": page, "period": returned_period}

    return all_extracted


def _hierarchy_keys(obj: dict) -> list[str]:
    """Return sorted hierarchy keys H1, H2, ... Hn present on obj."""
    keys = [k for k in obj if _H_LEVEL_RE.match(k)]
    keys.sort(key=lambda k: int(k[1:]))
    return keys


def build_schema_rows(
    schema: dict, extracted: dict, period_info: dict, date_value: str = ""
) -> list[dict]:
    """Map extracted Item->{value,page,period} into schema column rows (supports H1..Hn)."""
    line_items = schema["line_items"]
    h_keys: list[str] = []
    seen: set[str] = set()
    for item in line_items:
        for key in _hierarchy_keys(item):
            if key not in seen:
                seen.add(key)
                h_keys.append(key)

    extra_cols = [*h_keys, "units", "SourcePage", "SourcePeriod"]
    columns = schema.get("columns") or (
        ["Date", "Value", "Item", "Category", *h_keys, "units", "SourcePage", "SourcePeriod"]
    )
    for key in extra_cols:
        if key not in columns:
            if "units" in columns:
                columns.insert(columns.index("units"), key)
            else:
                columns.append(key)

    rows = []
    for item in line_items:
        name = item["Item"]
        entry = extracted.get(name) or {}
        value = entry.get("value") if isinstance(entry, dict) else entry
        page = entry.get("page") if isinstance(entry, dict) else None
        source_period = entry.get("period") if isinstance(entry, dict) else None
        row = {
            "Date": date_value,
            "Value": value,
            "Item": name,
            "Category": item.get("Category", ""),
            "units": item.get("units", ""),
            "SourcePage": page if page is not None else "",
            "SourcePeriod": source_period if source_period is not None else "",
        }
        for key in h_keys:
            row[key] = item.get(key, "")
        rows.append({col: row.get(col, "") for col in columns})
    return rows


def save_extracted(rows: list[dict], output_path: Path) -> Path:
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    return output_path


def _is_present(value) -> bool:
    """True when value is usable (not None/blank/-- placeholder)."""
    if value is None:
        return False
    text = str(value).strip()
    return bool(text) and text != "--"


def update_Datastore(
    rows: list[dict],
    granularity: str,
    scriptID: str,
    source: str,
    dataName: str,
    kind: str = "StagingData_v1",
) -> None:
    """
    Write each extracted row as a Datastore entity.

    Rows with a missing (None/"") Value are skipped rather than written as
    0, since a genuine 0, a missing value, and a wrong-period rejection are
    no longer conflated upstream.

    Reads all row attributes. Hierarchy fields H1..Hn are written dynamically
    (whatever levels are present on the row). Shared fields match spyder
    staging writes: item, value, dataName, publishedTS, dateTime, granularity,
    scriptID, source — plus Category, units, SourcePage, SourcePeriod when present.
    """
    for row in rows:
        item = row.get("Item") or row.get("item")
        if not item:
            raise ValueError(f"Row missing Item: {row!r}")

        value = row.get("Value", row.get("value"))
        if not _is_present(value):
            print(f"  Skipping datastore write for {item!r}: no grounded/period-matched value found")
            continue

        date_raw = row.get("Date") or row.get("date") or ""
        date_time = get_last_date_of_month(str(date_raw)) if date_raw else ""

        entity = {
            "item": item,
            "value": value,
            "dataName": dataName,
            "publishedTS": datetime.now(),
            "dateTime": date_time,
            "granularity": granularity,
            "scriptID": scriptID,
            "source": source,
        }

        category = row.get("Category", row.get("category"))
        if _is_present(category):
            entity["Category"] = category

        units = row.get("units")
        if _is_present(units):
            entity["units"] = units

        source_page = row.get("SourcePage")
        if _is_present(source_page):
            entity["SourcePage"] = source_page

        source_period = row.get("SourcePeriod")
        if _is_present(source_period):
            entity["SourcePeriod"] = source_period

        # Write every hierarchy level present: H1, H2, ... Hn.
        for key in _hierarchy_keys(row):
            level_val = row.get(key)
            if _is_present(level_val):
                entity[key] = level_val

        hierarchy_id = "#".join(
            str(entity[k]) for k in _hierarchy_keys(entity) if k in entity
        )
        id_parts = [dataName, entity.get("Category", ""), hierarchy_id, item, date_time]
        id_or_name = "#".join(p for p in id_parts if p)
        update_entity(entity, kind=kind, id_or_name=id_or_name)


def main():
    (
        folder_name,
        report_type,
        company_dir,
        pdf_path,
        vectors_path,
        schema_path,
        output_path,
        period_info,
    ) = prompt_run_inputs()
    print(f"Company folder: {company_dir}")
    print(f"PDF:            {pdf_path}")
    print(f"Vectors JSON:   {vectors_path}")
    print(f"Schema:         {schema_path}")
    print(f"Output JSON:    {output_path}")
    print(f"Target period:  {period_info['target_period']} ({period_info['as_printed']})")
    if period_info["exclude_labels"]:
        print(f"Excluding:      {period_info['exclude_labels']}")

    is_quarterly = report_type == "QR"
    granularity = "Quarterly" if is_quarterly else "Yearly"
    scriptID = f"{folder_name}"
    URL = report_type
    dataName = (
        f"{folder_name} Quarterly Report"
        if is_quarterly
        else f"{folder_name} Annual Report"
    )
    staging_kind = "StagingData_v1"

    schema = load_ar_schema(schema_path)
    line_items = schema["line_items"]
    print(f"Loaded {len(line_items)} line items from {schema_path.name}")

    vectors = load_vectors(vectors_path)
    print(f"Loaded {len(vectors)} embeddings from {vectors_path}")

    api_key = get_gemini_api_key()
    chunks_by_category = retrieve_chunks_for_schema_items(
        api_key, vectors, line_items, period_info, top_k=TOP_K_CHUNKS
    )
    total_chunks = sum(len(v) for v in chunks_by_category.values())
    print(f"Retrieved {total_chunks} chunk-references across {len(chunks_by_category)} categories")

    extracted = extract_values_with_gemini(api_key, chunks_by_category, line_items, period_info)
    rows = build_schema_rows(schema, extracted, period_info, date_value=period_info["period_end_date"])
    save_extracted(rows, output_path)

    print("Extracted rows:")
    print(json.dumps(rows, indent=2))
    print(f"Saved final JSON to {output_path}")

    # update_Datastore(
    #     rows,
    #     granularity,
    #     scriptID,
    #     URL,
    #     dataName,
    #     staging_kind,
    # )


if __name__ == "__main__":
    main()