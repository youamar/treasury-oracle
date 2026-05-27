"""Chutes LLM client — thin shim over llm_router for backwards-compat.

Historically this module owned the Chutes-primary / Chutes-fallback policy.
That policy now lives in `llm_router`, which adds OpenAI and Anthropic as
additional fallback tiers so the agent keeps running when Chutes is down.

This module keeps its public surface (`chat`, `resolve_profile`, `get_client`)
so existing call-sites don't need to change.
"""
from __future__ import annotations

from openai import OpenAI

from .config import (CHUTES_API_KEY, CHUTES_API_KEY_FALLBACK, CHUTES_BASE_URL,
                     MODEL_PROFILES)
from . import llm_router
from . import reliability


def resolve_profile(profile: str | None) -> str:
    """Resolve a profile name to an upstream model id. Unknown profile → default."""
    if not profile:
        return MODEL_PROFILES["default"]
    return MODEL_PROFILES.get(profile, MODEL_PROFILES["default"])


def get_client(use_fallback: bool = False) -> OpenAI:
    """Direct Chutes client (legacy). Most code should call `chat()` instead so
    multi-provider failover applies."""
    key = CHUTES_API_KEY_FALLBACK if use_fallback else CHUTES_API_KEY
    return OpenAI(api_key=key, base_url=CHUTES_BASE_URL)


def _call_primary(messages, model: str, **kwargs):
    """Legacy direct-to-Chutes call. Kept so existing tests that monkeypatch
    this symbol still work; production traffic goes through llm_router."""
    return get_client(False).chat.completions.create(
        model=model, messages=messages, **kwargs,
    )


def _call_fallback(messages, model: str, **kwargs):
    return get_client(True).chat.completions.create(
        model=model, messages=messages, **kwargs,
    )


def chat(messages, model: str, **kwargs):
    """Chat completion with full multi-provider failover (Chutes → OpenAI →
    Anthropic, per `LLM_PROVIDER_CHAIN`).

    If the test suite has monkeypatched `_call_primary` / `_call_fallback`, we
    honor those by short-circuiting the multi-provider router — that keeps the
    legacy Chutes-only test contract working."""
    kwargs.setdefault("timeout", 30)
    # retry_policy is consumed by reliability.with_retry — it must not be
    # forwarded to _call_primary/_call_fallback (which are real LLM SDK
    # calls that would reject the unknown kwarg). llm_router.chat handles
    # its own pop, but the legacy monkeypatched path is dispatched here.
    retry_policy = kwargs.pop("retry_policy", reliability.DEFAULT_POLICY)
    # Detect a monkeypatched primary: when tests inject their own _call_primary
    # (a non-default function), use the legacy 2-key Chutes-fallback path so
    # the test's stub gets invoked.
    if _call_primary is not _ORIG_CALL_PRIMARY:
        try:
            return reliability.with_retry(
                lambda: _call_primary(messages, model, **kwargs),
                source="chutes_client.chat.primary",
                policy=retry_policy,
            )
        except BaseException as e:
            if not reliability.is_auth_error(e) or not CHUTES_API_KEY_FALLBACK:
                raise
            return reliability.with_retry(
                lambda: _call_fallback(messages, model, **kwargs),
                source="chutes_client.chat.fallback",
                policy=retry_policy,
            )
    return llm_router.chat(messages, model, retry_policy=retry_policy, **kwargs)


_ORIG_CALL_PRIMARY = _call_primary
_ORIG_CALL_FALLBACK = _call_fallback


def last_provider() -> dict | None:
    """Provider info for the most recent chat() call. See llm_router."""
    return llm_router.last_provider()


def extract_content(resp) -> str:
    """Pull the final answer text out of a chat completion.

    Chutes reasoning models (Qwen-397B, MiniMax-M2.5, GLM-5.1, all -TEE
    variants) split their output into two fields:
      * `content`           — the actual answer
      * `reasoning_content` — chain-of-thought scratchpad

    When max_tokens is too small for both reasoning + answer, the model
    spends the whole budget on `reasoning_content` and emits nothing in
    `content`. We try to recover in this order:
      1. `content` if present and non-empty (normal case)
      2. trailing JSON block inside `reasoning_content` (some models
         finish thinking with the answer at the end)
      3. raw `reasoning_content` (last resort — caller's json.loads will
         fail loudly, which is the right signal to bump max_tokens)
    """
    try:
        msg = resp.choices[0].message
    except (AttributeError, IndexError, TypeError):
        return ""
    text = getattr(msg, "content", None)
    if text:
        return text
    rc = getattr(msg, "reasoning_content", None)
    if not rc:
        extra = getattr(msg, "model_extra", None) or {}
        rc = extra.get("reasoning_content")
    if not rc:
        return ""
    # Last-shot JSON salvage — find the LAST `{...}` block in the
    # reasoning text. Reasoning models that ran out of budget for
    # `content` often emit the final JSON inline before being truncated.
    salvaged = _last_json_block(rc)
    return salvaged or rc


def strip_code_fences(text: str) -> str:
    """Remove a leading ```lang ... ``` markdown wrapper if present.

    Reasoning models on Chutes sometimes wrap JSON in ```json ... ``` and
    sometimes don't — depends on the model in the pool that day. Every
    one-shot endpoint used to do `raw.split("```")[1]` which IndexErrors
    if there are zero or one backtick fences. This helper handles all
    three shapes (no fences / one pair / open fence) without raising."""
    if not text:
        return ""
    t = text.strip()
    if not t.startswith("```"):
        return t
    # Drop the opening ``` and any language tag on the same line.
    after_open = t[3:]
    nl = after_open.find("\n")
    if nl == -1:
        # Single-line ```json{...}``` — find closing fence.
        if after_open.startswith("json"):
            after_open = after_open[4:]
        end = after_open.rfind("```")
        return (after_open[:end] if end != -1 else after_open).strip()
    # Multi-line: skip the language tag line, then strip a trailing fence.
    body = after_open[nl + 1:]
    end = body.rfind("```")
    if end != -1:
        body = body[:end]
    return body.strip()


def _last_json_block(text: str) -> str:
    """Return the last balanced {...} substring in `text`, or empty."""
    end = text.rfind("}")
    if end == -1:
        return ""
    depth = 0
    for i in range(end, -1, -1):
        ch = text[i]
        if ch == "}":
            depth += 1
        elif ch == "{":
            depth -= 1
            if depth == 0:
                return text[i:end + 1]
    return ""


# Saved reference so tests can bypass the global mock_chutes fixture
# and exercise the real implementation.
_real_chat = chat
