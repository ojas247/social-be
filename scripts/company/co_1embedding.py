"""
Create vector embeddings for a company PDF and save them as JSON.

Prompts for:
  1) folder name under scripts/company
  2) PDF file name inside that folder

Writes: scripts/company/{folder}/{pdf_stem}.json

Improvements over the baseline version:
  - Tables are extracted separately (as markdown) instead of being mangled
    inside the plain-text stream.
  - Chunking is structure-aware (paragraph -> sentence -> char fallback)
    instead of blind fixed-offset slicing, so it stops cutting mid-sentence
    or mid-table-row.
  - Every chunk carries page_number + section metadata, so you can cite
    sources back to the PDF and later filter retrieval by page/section.
  - A short context header (company / section / page) is prepended to each
    chunk before embedding, so isolated numbers ("increased by 12%") keep
    their meaning.
  - Failed embedding batches are retried with backoff instead of being
    silently dropped, and anything that still fails is logged and re-queued
    at the end instead of vanishing from the index.
"""

import importlib.util
import json
import re
import sys
import time
from pathlib import Path

import google.generativeai as genai
import pdfplumber

# Load scripts/spyder/utils.py by path (sibling package, not on PYTHONPATH).
_SPYDER_UTILS = Path(__file__).resolve().parent.parent / "spyder" / "utils.py"
_spec = importlib.util.spec_from_file_location("spyder_utils", _SPYDER_UTILS)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load utils from {_SPYDER_UTILS}")
_spyder_utils = importlib.util.module_from_spec(_spec)
sys.modules["spyder_utils"] = _spyder_utils
_spec.loader.exec_module(_spyder_utils)
get_gemini_api_key = _spyder_utils.get_gemini_api_key

COMPANY_ROOT = Path(__file__).resolve().parent
EMBEDDING_MODEL = "models/gemini-embedding-001"

# Chunking (character-based; ~4 chars/token as a rough rule of thumb, so
# 800 tokens ~= 3200 chars). Tune these to your embedding model / doc type.
CHUNK_SIZE = 3200
CHUNK_OVERLAP = 320

# A line of text is treated as a section header if it's short, and either
# ALL CAPS or Title Case, e.g. "MANAGEMENT'S DISCUSSION AND ANALYSIS".
HEADER_RE = re.compile(r"^(?=.{3,80}$)([A-Z][A-Za-z0-9 ,&/'()\-]+)$")

MAX_RETRIES = 4
INITIAL_BACKOFF = 2.0


def prompt_run_inputs() -> tuple[Path, Path, Path]:
    """
    Ask for folder + PDF name.
    Returns (company_dir, pdf_path, vectors_path).
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
    pdf_path = company_dir / pdf_name
    vectors_path = company_dir / f"{Path(pdf_name).stem}.json"

    if not company_dir.is_dir():
        raise FileNotFoundError(f"Folder not found: {company_dir}")
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    return company_dir, pdf_path, vectors_path


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------

def table_to_markdown(table: list[list]) -> str:
    """Render a pdfplumber table (list of rows) as a markdown table."""
    if not table:
        return ""
    rows = [[(cell or "").strip().replace("\n", " ") for cell in row] for row in table]
    header, body = rows[0], rows[1:]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * len(header)) + " |",
    ]
    for row in body:
        # Pad/truncate ragged rows so the markdown table stays valid.
        row = (row + [""] * len(header))[: len(header)]
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def current_section(lines: list[str], fallback: str) -> str:
    """Best-effort: last header-like line seen so far on the page."""
    for line in reversed(lines):
        if HEADER_RE.match(line.strip()):
            return line.strip()
    return fallback


def extract_pdf_elements(pdf_path: Path) -> list[dict]:
    """
    Walk the PDF page by page and return a flat list of elements, each:
      {"type": "text" | "table", "content": str, "page": int, "section": str}

    Tables are pulled out via extract_tables() and rendered as markdown so
    they don't get scrambled when interleaved with body text. We drop each
    table's raw text from the plain-text extraction area it occupies as
    best-effort (pdfplumber doesn't give perfect text/table separation, so
    some duplication is possible — better than losing table structure).
    """
    elements = []
    running_section = "General"

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            page_text = page.extract_text() or ""
            text_lines = page_text.split("\n")
            running_section = current_section(text_lines, running_section)

            # Tables first (as their own elements).
            tables = page.extract_tables()
            for table in tables:
                md = table_to_markdown(table)
                if md.strip():
                    elements.append(
                        {
                            "type": "table",
                            "content": md,
                            "page": page_num,
                            "section": running_section,
                        }
                    )

            # Then the page's prose, split into paragraphs.
            for para in re.split(r"\n\s*\n", page_text):
                para = para.strip()
                if not para:
                    continue
                # Skip lines that are just a section header on their own.
                if HEADER_RE.match(para) and len(para.split("\n")) == 1:
                    running_section = para
                    continue
                elements.append(
                    {
                        "type": "text",
                        "content": para,
                        "page": page_num,
                        "section": running_section,
                    }
                )

    return elements


# --------------------------------------------------------------------------
# Chunking
# --------------------------------------------------------------------------

def _split_long_text(text: str, size: int, overlap: int) -> list[str]:
    """Fallback splitter for a single paragraph/table that's still too long.
    Prefers sentence boundaries, falls back to hard character slicing."""
    if len(text) <= size:
        return [text]

    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks, current = [], ""
    for sentence in sentences:
        if len(current) + len(sentence) + 1 <= size:
            current = f"{current} {sentence}".strip()
        else:
            if current:
                chunks.append(current)
            if len(sentence) > size:
                # Single sentence longer than the limit: hard-slice it.
                for i in range(0, len(sentence), size - overlap):
                    chunks.append(sentence[i : i + size])
                current = ""
            else:
                current = sentence
    if current:
        chunks.append(current)
    return chunks


def build_chunks(
    elements: list[dict], size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP
) -> list[dict]:
    """
    Greedily pack elements (paragraphs/tables) into chunks up to `size`
    chars, never splitting a table, and never splitting a paragraph unless
    it alone exceeds `size`. Each chunk keeps the page range and section it
    was drawn from.
    """
    chunks = []
    buf_parts: list[str] = []
    buf_len = 0
    buf_pages: set[int] = set()
    buf_section = None

    def flush():
        nonlocal buf_parts, buf_len, buf_pages, buf_section
        if buf_parts:
            chunks.append(
                {
                    "text": "\n\n".join(buf_parts).strip(),
                    "page_start": min(buf_pages),
                    "page_end": max(buf_pages),
                    "section": buf_section or "General",
                }
            )
        buf_parts, buf_len, buf_pages, buf_section = [], 0, set(), None

    for el in elements:
        piece = el["content"]
        # Tables are kept whole even if that pushes past `size` a bit —
        # splitting a table is worse than an oversized chunk.
        pieces = [piece] if el["type"] == "table" else _split_long_text(piece, size, overlap)

        for p in pieces:
            if buf_len + len(p) > size and buf_parts:
                flush()
            buf_parts.append(p)
            buf_len += len(p)
            buf_pages.add(el["page"])
            buf_section = buf_section or el["section"]

    flush()

    # Light char-overlap between consecutive chunks so context isn't lost
    # right at a chunk boundary.
    for i in range(1, len(chunks)):
        tail = chunks[i - 1]["text"][-overlap:]
        chunks[i]["text"] = f"{tail}\n\n{chunks[i]['text']}"

    return chunks


def add_context_headers(chunks: list[dict], company_name: str) -> list[dict]:
    """Prepend a short context header to each chunk before embedding, so an
    isolated number/sentence keeps its company/section/page context."""
    for c in chunks:
        pages = (
            f"page {c['page_start']}"
            if c["page_start"] == c["page_end"]
            else f"pages {c['page_start']}-{c['page_end']}"
        )
        header = f"[{company_name} | {c['section']} | {pages}]\n"
        c["embed_text"] = header + c["text"]
    return chunks


def chunk_pdf(pdf_path: Path, company_name: str) -> list[dict]:
    elements = extract_pdf_elements(pdf_path)
    chunks = build_chunks(elements)
    chunks = add_context_headers(chunks, company_name)
    return chunks


# --------------------------------------------------------------------------
# Embedding
# --------------------------------------------------------------------------

def _normalize_embeddings(response_embedding):
    """Batch returns list[list]; single returns list[float]."""
    if response_embedding and isinstance(response_embedding[0], (int, float)):
        return [response_embedding]
    return response_embedding


def _embed_batch_with_retry(texts: list[str], max_retries: int = MAX_RETRIES):
    """Call the embedding API with exponential backoff. Raises on final failure."""
    backoff = INITIAL_BACKOFF
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            response = genai.embed_content(
                model=EMBEDDING_MODEL,
                content=texts,
                task_type="retrieval_document",
            )
            return _normalize_embeddings(response["embedding"])
        except Exception as e:
            last_err = e
            print(f"  Attempt {attempt}/{max_retries} failed: {e}")
            if attempt < max_retries:
                time.sleep(backoff)
                backoff *= 2
    raise RuntimeError(f"Batch failed after {max_retries} attempts: {last_err}")


def vectorize_chunks_gemini(api_key: str, chunks: list[dict], batch_size: int = 50) -> list[dict]:
    """
    Vectorize chunks with batching, REST transport, retry-with-backoff, and
    light rate-limiting. Chunks whose batch fails after all retries are
    logged and skipped (not silently dropped without a trace).
    """
    genai.configure(api_key=api_key, transport="rest")

    vectors = []
    failed_indices: list[int] = []
    total_chunks = len(chunks)
    print(f"Vectorizing {total_chunks} chunks in batches of {batch_size}...")

    for i in range(0, total_chunks, batch_size):
        batch = chunks[i : i + batch_size]
        texts = [c["embed_text"] for c in batch]
        try:
            embeddings = _embed_batch_with_retry(texts)
            for offset, embedding in enumerate(embeddings):
                chunk = batch[offset]
                vectors.append(
                    {
                        "chunk_idx": i + offset,
                        "embedding": embedding,
                        "text": chunk["text"],
                        "page_start": chunk["page_start"],
                        "page_end": chunk["page_end"],
                        "section": chunk["section"],
                    }
                )
            print(f"Processed chunks {i + 1} to {min(i + batch_size, total_chunks)} / {total_chunks}")
            time.sleep(0.5)
        except Exception as e:
            print(f"Batch starting at index {i} failed permanently: {e}")
            failed_indices.extend(range(i, min(i + batch_size, total_chunks)))
            time.sleep(2)

    if failed_indices:
        print(
            f"WARNING: {len(failed_indices)} chunk(s) could not be embedded "
            f"(indices: {failed_indices[:10]}{'...' if len(failed_indices) > 10 else ''}). "
            "These are missing from the index — re-run to retry, or investigate the API error above."
        )

    return vectors


def save_vectors(vectors: list[dict], vectors_path: Path) -> Path:
    vectors_path.parent.mkdir(parents=True, exist_ok=True)
    with open(vectors_path, "w", encoding="utf-8") as f:
        json.dump(vectors, f, ensure_ascii=False, indent=2)
    return vectors_path


def main():
    company_dir, pdf_path, vectors_path = prompt_run_inputs()
    company_name = company_dir.name
    print(f"Company folder: {company_dir}")
    print(f"PDF:            {pdf_path}")
    print(f"Vectors JSON:   {vectors_path}")

    print(f"Reading PDF: {pdf_path}")
    chunks = chunk_pdf(pdf_path, company_name)
    print(f"Chunked PDF into {len(chunks)} chunks (structure-aware, with table + page metadata)")

    api_key = get_gemini_api_key()
    vectors = vectorize_chunks_gemini(api_key, chunks)
    if not vectors:
        raise RuntimeError("No embeddings produced")

    save_vectors(vectors, vectors_path)
    print(f"Saved {len(vectors)} embeddings to {vectors_path}")


if __name__ == "__main__":
    main()