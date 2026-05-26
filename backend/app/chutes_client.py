"""Chutes LLM client with retry, timeout, and a *narrow* fallback policy.

Previous behaviour swapped to the fallback key on ANY exception, which masked
real bugs (bad payloads, schema errors) as auth failures. We now:

  - Retry retryable transports (429/5xx/timeouts) via reliability.with_retry.
  - Swap to the fallback key ONLY on a confirmed auth/permission failure on
    the primary key, then retry once on the fallback.
  - Record both auth-fallbacks and final failures into the `errors` table.
"""
from __future__ import annotations

from openai import OpenAI

from .config import (CHUTES_API_KEY, CHUTES_API_KEY_FALLBACK, CHUTES_BASE_URL,
                     MODEL_PROFILES)
from . import reliability


def resolve_profile(profile: str | None) -> str:
    """Resolve a profile name to an upstream model id. Unknown profile → default."""
    if not profile:
        return MODEL_PROFILES["default"]
    return MODEL_PROFILES.get(profile, MODEL_PROFILES["default"])


def get_client(use_fallback: bool = False) -> OpenAI:
    key = CHUTES_API_KEY_FALLBACK if use_fallback else CHUTES_API_KEY
    return OpenAI(api_key=key, base_url=CHUTES_BASE_URL)


def _call_primary(messages, model: str, **kwargs):
    return get_client(False).chat.completions.create(
        model=model, messages=messages, **kwargs,
    )


def _call_fallback(messages, model: str, **kwargs):
    return get_client(True).chat.completions.create(
        model=model, messages=messages, **kwargs,
    )


def chat(messages, model: str, **kwargs):
    """Call Chutes chat completion with retry + narrow auth-only fallback."""
    kwargs.setdefault("timeout", 30)
    try:
        return reliability.with_retry(
            lambda: _call_primary(messages, model, **kwargs),
            source="chutes_client.chat.primary",
        )
    except BaseException as e:
        if not reliability.is_auth_error(e) or not CHUTES_API_KEY_FALLBACK:
            reliability.record_error(
                "chutes_client.chat",
                e,
                context={"model": model, "stage": "primary",
                         "retryable": reliability.is_retryable(e)},
            )
            raise
        reliability.record_error(
            "chutes_client.chat",
            e,
            context={"model": model, "stage": "primary_auth_fallback"},
            kind="auth_fallback",
        )
        try:
            return reliability.with_retry(
                lambda: _call_fallback(messages, model, **kwargs),
                source="chutes_client.chat.fallback",
            )
        except BaseException as e2:
            reliability.record_error(
                "chutes_client.chat", e2,
                context={"model": model, "stage": "fallback"},
            )
            raise


# Saved reference so tests can bypass the global mock_chutes fixture
# and exercise the real implementation.
_real_chat = chat
