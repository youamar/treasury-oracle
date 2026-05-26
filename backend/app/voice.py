"""Voice note ingestion.

Strategy: try Chutes' audio model if available, else fall back to having the
LLM read a *transcribed* text the user provides. For the hackathon, we keep
the dependency footprint small — full local Whisper is optional and gated
behind an env flag.

For demo robustness, if no transcription backend works, we accept a
manually-supplied transcript via the same endpoint as a string field.
"""
import io
from .chutes_client import chat
from .ocr import extract_payment_proof
from .config import REASONING_MODEL


VOICE_PARSE_PROMPT = """The user sent a voice note about a payment. Below is the
transcript. Extract structured payment-proof fields. Return ONLY JSON:

{{
  "amount": <number or null>,
  "currency": "<ISO 4217 or null>",
  "date": "<YYYY-MM-DD or null>",
  "payer": "<name or null>",
  "payee": "<name or null>",
  "reference": "<invoice ref or null>",
  "description": "<short summary or null>"
}}

Transcript (may be in any language):
\"\"\"
{transcript}
\"\"\"
"""


def transcribe_audio(audio_bytes: bytes, filename: str) -> str:
    """
    Try to transcribe via local openai-whisper if installed; else raise.
    The endpoint is also designed to accept a pre-transcribed string so the
    demo doesn't require model downloads.
    """
    try:
        import whisper  # type: ignore
    except ImportError:
        raise RuntimeError(
            "Whisper not installed. Either `pip install openai-whisper` or "
            "POST the transcript directly via the `transcript` form field."
        )
    import tempfile, os
    suffix = "." + (filename.rsplit(".", 1)[-1] if "." in filename else "wav")
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(audio_bytes)
        path = tmp.name
    try:
        model = whisper.load_model("base")
        result = model.transcribe(path)
        return result["text"]
    finally:
        try: os.unlink(path)
        except OSError: pass


def extract_from_transcript(transcript: str, source: str = "voice_note") -> dict:
    """Extract payment fields from a free-text transcript via LLM.

    Always returns a dict shaped like an OCR proof, plus `transcript` and
    `source_file`. On any failure (LLM error, bad JSON, no usable fields)
    the dict includes a top-level `error` so the UI can surface it as a
    warning rather than polluting the proof list with a half-empty row.
    """
    import json
    transcript = (transcript or "").strip()
    if not transcript:
        return {"error": "empty transcript", "source_file": source,
                "transcript": transcript}

    prompt = VOICE_PARSE_PROMPT.format(transcript=transcript[:4000])
    try:
        resp = chat(
            messages=[{"role": "user", "content": prompt}],
            model=REASONING_MODEL,
            temperature=0.1,
            max_tokens=500,
            response_format={"type": "json_object"},
        )
    except Exception as e:
        return {"error": f"LLM call failed: {type(e).__name__}: {e}",
                "source_file": source, "transcript": transcript}

    raw = (resp.choices[0].message.content or "").strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        if len(parts) >= 2:
            inner = parts[1]
            if inner.startswith("json"):
                inner = inner[4:]
            raw = inner.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "could not parse model output as JSON",
                "raw": raw[:300], "source_file": source, "transcript": transcript}

    # If the LLM returned something but the critical fields are all missing,
    # treat that as a soft failure — better than letting an empty 'proof'
    # silently enter the reconciliation pipeline.
    if not data.get("amount") or not data.get("currency"):
        data["error"] = "transcript didn't contain a parseable amount + currency"
    data["source_file"] = source
    data["transcript"] = transcript
    return data
