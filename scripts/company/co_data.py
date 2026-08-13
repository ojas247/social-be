"""
Load cached vector embeddings and extract AR_schema line items via RAG + LLM.

Prompts for:
  1) folder name under scripts/company
  2) PDF file name (used to locate {pdf_stem}.json embeddings)

Requires:
  - scripts/company/{folder}/{pdf_stem}.json   (from co_embedding.py)
  - scripts/company/{folder}/AR_schema.json

Writes:
  - scripts/company/{folder}/{pdf_stem}_extracted.json
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
AR_SCHEMA_FILENAME = "AR_schema.json"
EMBEDDING_MODEL = "models/gemini-embedding-001"
TOP_K_CHUNKS = 5
_H_LEVEL_RE = re.compile(r"^H\d+$")


def prompt_run_inputs() -> tuple[str, Path, Path, Path, Path, Path]:
    """
    Ask for folder + PDF name.
    Returns (folder_name, company_dir, pdf_path, vectors_path, schema_path, output_path).
    """
    folder_name = input("Folder name (under scripts/company): ").strip().strip('"').strip("'")
    pdf_name = input("PDF file name: ").strip().strip('"').strip("'")
    if not folder_name:
        raise ValueError("Folder name is required")
    if not pdf_name:
        raise ValueError("PDF file name is required")
    if not pdf_name.lower().endswith(".pdf"):
        pdf_name = f"{pdf_name}.pdf"

    company_dir = COMPANY_ROOT / folder_name
    stem = Path(pdf_name).stem
    pdf_path = company_dir / pdf_name
    vectors_path = company_dir / f"{stem}.json"
    schema_path = company_dir / AR_SCHEMA_FILENAME
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

    return folder_name, company_dir, pdf_path, vectors_path, schema_path, output_path


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


def embed_query(api_key: str, query: str) -> list[float]:
    """Embed a retrieval query with the same model used for documents."""
    genai.configure(api_key=api_key, transport="rest")
    response = genai.embed_content(
        model=EMBEDDING_MODEL,
        content=query,
        task_type="retrieval_query",
    )
    embedding = response["embedding"]
    if embedding and isinstance(embedding[0], list):
        return embedding[0]
    return embedding


def retrieve_nearest_chunks(
    vectors: list[dict],
    query_embedding: list[float],
    top_k: int = TOP_K_CHUNKS,
) -> list[dict]:
    """Return top_k document chunks by cosine similarity to the query embedding."""
    if not vectors:
        return []
    scored = [
        {
            **item,
            "score": cosine_similarity(query_embedding, item["embedding"]),
        }
        for item in vectors
    ]
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[: max(1, top_k)]


def _line_item_label(item: dict) -> str:
    parts = [
        item.get("Category") or "",
        item.get("H1") or "",
        item.get("H2") or "",
        item.get("Item") or "",
    ]
    return " > ".join(p for p in parts if p and p != "--")


def retrieve_chunks_for_schema_items(
    api_key: str,
    vectors: list[dict],
    line_items: list[dict],
    top_k: int = TOP_K_CHUNKS,
) -> list[dict]:
    """
    For each AR_schema line item, embed a query and take top_k nearest chunks.
    Merge unique chunks, keeping the best score per chunk_idx.
    """
    best_by_idx: dict[int, dict] = {}
    for item in line_items:
        label = _line_item_label(item)
        units = item.get("units") or ""
        query = (
            f"Find the absolute numeric value for '{item.get('Item')}' "
            f"({label}; units: {units}) from the company annual report."
        )
        query_embedding = embed_query(api_key, query)
        nearest = retrieve_nearest_chunks(vectors, query_embedding, top_k=top_k)
        print(f"Nearest chunks for {label!r}:")
        for hit in nearest:
            print(f"  chunk={hit['chunk_idx']} score={hit['score']:.4f}")
            prev = best_by_idx.get(hit["chunk_idx"])
            if prev is None or hit["score"] > prev["score"]:
                best_by_idx[hit["chunk_idx"]] = hit
        time.sleep(0.2)

    return sorted(best_by_idx.values(), key=lambda x: x["score"], reverse=True)


def extract_values_with_gemini(api_key, relevant_chunks, line_items: list[dict]):
    genai.configure(api_key=api_key, transport="rest")
    model = genai.GenerativeModel("gemini-2.5-pro")
    context = "\n\n---\n\n".join(
        f"[chunk {c['chunk_idx']} | score={c.get('score', 0):.4f}]\n{c['text']}"
        for c in relevant_chunks
    )
    item_names = [item["Item"] for item in line_items]
    output_schema = {name: "value_here" for name in item_names}
    prompt = f"""You will extract only the required values as strict JSON.

Task: Extract absolute numeric values for these annual-report line items from the source chunks.
Use the Item name as the JSON key. Hierarchy metadata is provided for disambiguation:
{json.dumps(line_items, indent=2)}

Rules:
1. Keys MUST exactly match the Item names listed above.
2. Values should be pure numbers (no currency symbols). Keep digits only; remove commas.
3. If an item is not found, set its value to 0.

Output JSON format:
{json.dumps(output_schema, indent=2)}

Source Data (nearest retrieved chunks only):
{context}
"""
    response = model.generate_content(prompt)
    return parse_json_from_llm_output(response.text)


def _hierarchy_keys(obj: dict) -> list[str]:
    """Return sorted hierarchy keys H1, H2, ... Hn present on obj."""
    keys = [k for k in obj if _H_LEVEL_RE.match(k)]
    keys.sort(key=lambda k: int(k[1:]))
    return keys


def build_schema_rows(schema: dict, extracted: dict, date_value: str = "") -> list[dict]:
    """Map extracted Item->Value into AR_schema column rows (supports H1..Hn)."""
    line_items = schema["line_items"]
    h_keys: list[str] = []
    seen: set[str] = set()
    for item in line_items:
        for key in _hierarchy_keys(item):
            if key not in seen:
                seen.add(key)
                h_keys.append(key)

    columns = schema.get("columns") or (
        ["Date", "Value", "Item", "Category", *h_keys, "units"]
    )
    # Ensure any Hn present on line items is retained even if columns omit it.
    for key in h_keys:
        if key not in columns:
            # Insert hierarchy keys before units when possible.
            if "units" in columns:
                columns.insert(columns.index("units"), key)
            else:
                columns.append(key)

    rows = []
    for item in line_items:
        name = item["Item"]
        value = extracted.get(name, 0)
        row = {
            "Date": date_value,
            "Value": value,
            "Item": name,
            "Category": item.get("Category", ""),
            "units": item.get("units", ""),
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
    Write each extracted AR row as a Datastore entity.

    Reads all row attributes. Hierarchy fields H1..Hn are written dynamically
    (whatever levels are present on the row). Shared fields match spyder
    staging writes: item, value, dataName, publishedTS, dateTime, granularity,
    scriptID, source — plus Category and units when present.
    """
    for row in rows:
        item = row.get("Item") or row.get("item")
        if not item:
            raise ValueError(f"Row missing Item: {row!r}")

        date_raw = row.get("Date") or row.get("date") or ""
        date_time = get_last_date_of_month(str(date_raw)) if date_raw else ""

        entity = {
            "item": item,
            "value": row.get("Value", row.get("value", 0)),
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
    folder_name, company_dir, pdf_path, vectors_path, schema_path, output_path = (
        prompt_run_inputs()
    )
    print(f"Company folder: {company_dir}")
    print(f"PDF:            {pdf_path}")
    print(f"Vectors JSON:   {vectors_path}")
    print(f"Schema:         {schema_path}")
    print(f"Output JSON:    {output_path}")

    # Datastore write params (same shape as scripts/spyder/*).
    month_label = "March 2025"  # FY end month/year, e.g. "March 2025"
    granularity = "Yearly"
    scriptID = f"{folder_name}"
    URL = str(pdf_path)
    dataName = f"{folder_name} Annual Report"
    staging_kind = "StagingData_v1"

    schema = load_ar_schema(schema_path)
    line_items = schema["line_items"]
    print(f"Loaded {len(line_items)} line items from {AR_SCHEMA_FILENAME}")

    vectors = load_vectors(vectors_path)
    print(f"Loaded {len(vectors)} embeddings from {vectors_path}")

    api_key = get_gemini_api_key()
    relevant_chunks = retrieve_chunks_for_schema_items(
        api_key, vectors, line_items, top_k=TOP_K_CHUNKS
    )
    print(f"Sending {len(relevant_chunks)} unique nearest chunks to LLM")

    extracted = extract_values_with_gemini(api_key, relevant_chunks, line_items)
    rows = build_schema_rows(schema, extracted, date_value=month_label)
    save_extracted(rows, output_path)

    print("Extracted rows:")
    print(json.dumps(rows, indent=2))
    print(f"Saved final JSON to {output_path}")

    update_Datastore(
        rows,
        granularity,
        scriptID,
        URL,
        dataName,
        staging_kind,
    )


if __name__ == "__main__":
    main()
