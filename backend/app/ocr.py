"""OCR agent — uses vision LLM to extract structured payment proof data."""
import asyncio
import base64
import json
import io
import os
from typing import Optional
from PIL import Image
from .chutes_client import chat, extract_content, strip_code_fences
from .config import VISION_MODEL


# Bound parallel OCR calls so we don't blow past upstream rate limits.
# Tunable via env. Defaults to 4 — empirically below Chutes' rate ceiling.
OCR_CONCURRENCY = int(os.getenv("OCR_CONCURRENCY", "4"))
_OCR_SEM = asyncio.Semaphore(OCR_CONCURRENCY)

# Completeness gate. Below this score, the proof goes straight to a human-
# review bucket — we don't spend agent tokens trying to reconcile garbage.
OCR_QUALITY_GATE = float(os.getenv("OCR_QUALITY_GATE", "0.6"))

# Field weights. Critical fields dominate the score; nice-to-have fields
# move it a little. Tune per-tenant if a customer's bank statements never
# include 'reference', for example.
_FIELD_WEIGHTS = {
    "amount":      0.30,
    "currency":    0.20,
    "date":        0.20,
    "payer":       0.10,
    "payee":       0.10,
    "reference":   0.05,
    "description": 0.05,
}


def _score_completeness(parsed: dict) -> dict:
    """Returns {completeness, missing_fields, gate}.

    completeness ∈ [0, 1]. Fields scored as missing when None, "", or absent.
    Numeric amount of 0 also counts as missing (a zero amount is meaningless
    for a payment proof — almost always an OCR failure)."""
    missing = []
    score = 0.0
    for field, weight in _FIELD_WEIGHTS.items():
        v = parsed.get(field)
        if v is None or v == "" or (field == "amount" and v == 0):
            missing.append(field)
        else:
            score += weight
    score = round(score, 3)
    return {
        "completeness": score,
        "missing_fields": missing,
        "gate": "ok" if score >= OCR_QUALITY_GATE else "low_quality",
    }


EXTRACTION_PROMPT = """You are a payment-proof OCR specialist. The user uploaded an image of a payment receipt, transfer screenshot, or invoice.

Extract the following fields and return ONLY valid JSON (no markdown, no commentary):

{
  "amount": <number, the transaction amount>,
  "currency": "<ISO 4217 code, e.g. USD, EUR, MYR, SGD>",
  "date": "<YYYY-MM-DD, the transaction date>",
  "payer": "<sender name or organisation, or null>",
  "payee": "<recipient name, or null>",
  "reference": "<invoice number or transaction reference, or null>",
  "description": "<short description of the transaction, or null>"
}

If a field is unreadable, use null. Be precise with the amount and date."""


def _image_to_data_url(image_bytes: bytes, mime: str = "image/png") -> str:
    b64 = base64.b64encode(image_bytes).decode()
    return f"data:{mime};base64,{b64}"


def _normalize_image(image_bytes: bytes) -> bytes:
    """Re-encode as PNG to ensure compatibility; downscale if huge."""
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    max_dim = 1600
    if max(img.size) > max_dim:
        img.thumbnail((max_dim, max_dim))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def extract_payment_proof(image_bytes: bytes, filename: str = "") -> dict:
    """Send image to vision LLM, parse structured JSON."""
    normalized = _normalize_image(image_bytes)
    data_url = _image_to_data_url(normalized, "image/png")

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": EXTRACTION_PROMPT},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }
    ]

    resp = chat(
        messages=messages,
        model=VISION_MODEL,
        temperature=0.1,
        max_tokens=600,
        response_format={"type": "json_object"},
    )
    raw = strip_code_fences(extract_content(resp))

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {"error": "could not parse model output", "raw": raw}

    data["source_file"] = filename
    # Quality gate — don't burn agent tokens trying to reconcile a proof the
    # vision LLM couldn't read. Errors automatically fail the gate.
    if "error" in data:
        data["ocr_quality"] = {"completeness": 0.0, "missing_fields": list(_FIELD_WEIGHTS),
                               "gate": "low_quality"}
    else:
        data["ocr_quality"] = _score_completeness(data)
    return data


async def extract_payment_proof_async(image_bytes: bytes, filename: str = "") -> dict:
    """Async wrapper — bounded by the global OCR semaphore."""
    async with _OCR_SEM:
        return await asyncio.to_thread(extract_payment_proof, image_bytes, filename)


async def extract_payment_proofs_batch(items: list[tuple[bytes, str]]) -> list[dict]:
    """Run many OCR calls concurrently (up to OCR_CONCURRENCY). Exceptions
    are converted into per-item error dicts so one bad image never tanks the batch."""
    coros = [extract_payment_proof_async(b, n) for b, n in items]
    results: list[dict] = []
    for r in await asyncio.gather(*coros, return_exceptions=True):
        if isinstance(r, BaseException):
            results.append({"error": f"{type(r).__name__}: {r}",
                            "source_file": "(unknown)"})
        else:
            results.append(r)
    # Re-attach the original filename where the call raised before reaching it
    for i, r in enumerate(results):
        if r.get("source_file") in (None, "(unknown)"):
            r["source_file"] = items[i][1]
    return results


def pdf_to_images(pdf_bytes: bytes) -> list[bytes]:
    """Convert PDF pages to PNG bytes. Requires poppler installed for pdf2image."""
    try:
        from pdf2image import convert_from_bytes
        pages = convert_from_bytes(pdf_bytes, dpi=200)
        out = []
        for p in pages:
            buf = io.BytesIO()
            p.save(buf, format="PNG")
            out.append(buf.getvalue())
        return out
    except Exception as e:
        raise RuntimeError(f"PDF conversion failed (is poppler installed?): {e}")
