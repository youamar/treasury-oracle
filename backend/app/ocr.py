"""OCR agent — uses vision LLM to extract structured payment proof data."""
import base64
import json
import io
from typing import Optional
from PIL import Image
from .chutes_client import chat
from .config import VISION_MODEL


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
    raw = resp.choices[0].message.content.strip()
    # Strip code fences if model wraps response
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {"error": "could not parse model output", "raw": raw}

    data["source_file"] = filename
    return data


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
