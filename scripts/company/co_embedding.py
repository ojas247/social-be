"""
Create vector embeddings for a company PDF and save them as JSON.

Prompts for:
  1) folder name under scripts/company
  2) PDF file name inside that folder

Writes: scripts/company/{folder}/{pdf_stem}.json
"""

import importlib.util
import json
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


def chunk_pdf_text(pdf_path, chunk_size=1000, overlap=100):
    """Read PDF, return list of text chunks with overlap."""
    with pdfplumber.open(pdf_path) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    chunks = []
    i = 0
    while i < len(text):
        chunks.append(text[i : i + chunk_size])
        i += chunk_size - overlap
    return chunks


def _normalize_embeddings(response_embedding):
    """Batch returns list[list]; single returns list[float]."""
    if response_embedding and isinstance(response_embedding[0], (int, float)):
        return [response_embedding]
    return response_embedding


def vectorize_chunks_gemini(api_key, text_chunks, batch_size=50):
    """Vectorize text chunks with batching, REST transport, and light rate-limiting."""
    genai.configure(api_key=api_key, transport="rest")

    vectors = []
    total_chunks = len(text_chunks)
    print(f"Vectorizing {total_chunks} chunks in batches of {batch_size}...")

    for i in range(0, total_chunks, batch_size):
        batch = text_chunks[i : i + batch_size]
        try:
            response = genai.embed_content(
                model=EMBEDDING_MODEL,
                content=batch,
                task_type="retrieval_document",
            )
            embeddings = _normalize_embeddings(response["embedding"])
            for offset, embedding in enumerate(embeddings):
                vectors.append(
                    {
                        "chunk_idx": i + offset,
                        "embedding": embedding,
                        "text": batch[offset],
                    }
                )
            print(
                f"Processed chunks {i + 1} to "
                f"{min(i + batch_size, total_chunks)} / {total_chunks}"
            )
            time.sleep(0.5)
        except Exception as e:
            print(f"Error at batch starting index {i}: {e}")
            time.sleep(2)

    return vectors


def save_vectors(vectors: list[dict], vectors_path: Path) -> Path:
    vectors_path.parent.mkdir(parents=True, exist_ok=True)
    with open(vectors_path, "w", encoding="utf-8") as f:
        json.dump(vectors, f, ensure_ascii=False, indent=2)
    return vectors_path


def main():
    company_dir, pdf_path, vectors_path = prompt_run_inputs()
    print(f"Company folder: {company_dir}")
    print(f"PDF:            {pdf_path}")
    print(f"Vectors JSON:   {vectors_path}")

    print(f"Reading PDF: {pdf_path}")
    chunks = chunk_pdf_text(pdf_path)
    print(f"Chunked PDF into {len(chunks)} chunks")

    api_key = get_gemini_api_key()
    vectors = vectorize_chunks_gemini(api_key, chunks)
    if not vectors:
        raise RuntimeError("No embeddings produced")

    save_vectors(vectors, vectors_path)
    print(f"Saved {len(vectors)} embeddings to {vectors_path}")


if __name__ == "__main__":
    main()
