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
    import json
    prompt = VOICE_PARSE_PROMPT.format(transcript=transcript[:4000])
    resp = chat(
        messages=[{"role": "user", "content": prompt}],
        model=REASONING_MODEL,
        temperature=0.1,
        max_tokens=500,
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {"error": "could not parse", "raw": raw}
    data["source_file"] = source
    data["transcript"] = transcript
    return data
