from openai import OpenAI
from .config import CHUTES_API_KEY, CHUTES_API_KEY_FALLBACK, CHUTES_BASE_URL


def get_client(use_fallback: bool = False) -> OpenAI:
    key = CHUTES_API_KEY_FALLBACK if use_fallback else CHUTES_API_KEY
    return OpenAI(api_key=key, base_url=CHUTES_BASE_URL)


def chat(messages, model: str, **kwargs):
    """Call Chutes chat completion with automatic fallback on the second API key."""
    try:
        client = get_client(False)
        return client.chat.completions.create(model=model, messages=messages, **kwargs)
    except Exception:
        client = get_client(True)
        return client.chat.completions.create(model=model, messages=messages, **kwargs)
