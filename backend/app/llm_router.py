"""Multi-LLM provider router.

Treasury Oracle must keep running even if a single LLM provider is down or
rate-limited. The router defines an ordered chain of providers; each call is
attempted in order until one succeeds. Per-call provider info is exposed via
a thread-local so the agent trace can record which provider answered.

Providers are env-gated — if the key isn't set, the provider is skipped.
Default chain (configurable via LLM_PROVIDER_CHAIN):

    chutes_primary, chutes_fallback, openai, anthropic

Each entry returns an object with the OpenAI shape:
    resp.choices[0].message.content (str)
    resp.choices[0].message.tool_calls (optional)
    resp.usage.prompt_tokens / completion_tokens (optional)

so call-sites that already speak OpenAI don't need to change.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx
from openai import OpenAI

from . import reliability
from .config import (CHUTES_API_KEY, CHUTES_API_KEY_FALLBACK, CHUTES_BASE_URL,
                     MODEL_PROFILES)


# ---------- per-call provider info (read by tracer) ----------

_local = threading.local()


def last_provider() -> dict | None:
    """Provider info for the most recent llm_router.chat() call on this thread.
    Shape: {"provider": "chutes_primary", "model": "...", "latency_ms": 123,
            "fallback_from": ["chutes_primary"]}"""
    return getattr(_local, "info", None)


def _set_last(info: dict) -> None:
    _local.info = info


# ---------- provider implementations ----------

@dataclass
class Provider:
    name: str
    enabled: bool
    call: Callable[..., Any]
    # Optional model override map: profile -> provider-specific model id.
    # Falls back to MODEL_PROFILES if not present.
    model_map: dict[str, str] | None = None

    def resolve_model(self, model: str) -> str:
        if self.model_map:
            for profile, upstream in MODEL_PROFILES.items():
                if upstream == model and profile in self.model_map:
                    return self.model_map[profile]
        return model


def _openai_compat_client(api_key: str, base_url: str) -> OpenAI:
    return OpenAI(api_key=api_key, base_url=base_url)


def _make_chutes_call(api_key: str):
    def _call(messages, model, **kw):
        client = _openai_compat_client(api_key, CHUTES_BASE_URL)
        return client.chat.completions.create(model=model, messages=messages, **kw)
    return _call


def _make_openai_call(api_key: str, base_url: str):
    def _call(messages, model, **kw):
        client = _openai_compat_client(api_key, base_url)
        return client.chat.completions.create(model=model, messages=messages, **kw)
    return _call


# ---------- Anthropic adapter (httpx, no SDK dep) ----------

class _AnthropicMessage:
    def __init__(self, content: str, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls
        self.role = "assistant"


class _AnthropicChoice:
    def __init__(self, message: _AnthropicMessage, finish_reason: str = "stop"):
        self.message = message
        self.finish_reason = finish_reason
        self.index = 0


class _AnthropicUsage:
    def __init__(self, prompt_tokens: int, completion_tokens: int):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens


class _AnthropicResponse:
    def __init__(self, content: str, usage: _AnthropicUsage, model: str):
        self.choices = [_AnthropicChoice(_AnthropicMessage(content))]
        self.usage = usage
        self.model = model


def _make_anthropic_call(api_key: str):
    """Anthropic Messages API → OpenAI-shaped response (text only, no tool use).

    The agent's tool-use path stays on OpenAI-compatible providers; Anthropic
    is the resilience tier for narrative / dunning / verifier text generation
    when both Chutes and OpenAI are down."""
    def _call(messages, model, **kw):
        # Split system message out (Anthropic takes it as a top-level field).
        system = ""
        msgs = []
        for m in messages:
            if m.get("role") == "system":
                system += (m.get("content") or "") + "\n"
            else:
                msgs.append({"role": m["role"], "content": m.get("content") or ""})
        body = {
            "model": model,
            "max_tokens": kw.get("max_tokens", 1024),
            "messages": msgs,
        }
        if system.strip():
            body["system"] = system.strip()
        if "temperature" in kw:
            body["temperature"] = kw["temperature"]
        timeout = kw.get("timeout", 30)
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=body,
            timeout=timeout,
        )
        if r.status_code >= 400:
            # Mimic OpenAI SDK error shape so reliability.is_retryable/is_auth_error work.
            err = httpx.HTTPStatusError(
                f"Anthropic {r.status_code}: {r.text[:200]}",
                request=r.request, response=r,
            )
            err.status_code = r.status_code  # type: ignore[attr-defined]
            raise err
        data = r.json()
        text_parts = [b.get("text", "") for b in data.get("content", [])
                      if b.get("type") == "text"]
        usage = data.get("usage", {})
        return _AnthropicResponse(
            content="".join(text_parts),
            usage=_AnthropicUsage(usage.get("input_tokens", 0),
                                  usage.get("output_tokens", 0)),
            model=data.get("model", model),
        )
    return _call


# ---------- chain construction ----------

# Anthropic model mapping — when a profile maps to a non-Anthropic model id,
# remap to a sensible Claude default so the fallback actually has a model.
ANTHROPIC_MODELS = {
    "default": os.getenv("ANTHROPIC_MODEL_DEFAULT", "claude-haiku-4-5-20251001"),
    "cheap":   os.getenv("ANTHROPIC_MODEL_CHEAP",   "claude-haiku-4-5-20251001"),
    "strong":  os.getenv("ANTHROPIC_MODEL_STRONG",  "claude-sonnet-4-5"),
    "vision":  os.getenv("ANTHROPIC_MODEL_VISION",  "claude-sonnet-4-5"),
}

OPENAI_MODELS = {
    "default": os.getenv("OPENAI_MODEL_DEFAULT", "gpt-4o-mini"),
    "cheap":   os.getenv("OPENAI_MODEL_CHEAP",   "gpt-4o-mini"),
    "strong":  os.getenv("OPENAI_MODEL_STRONG",  "gpt-4o"),
    "vision":  os.getenv("OPENAI_MODEL_VISION",  "gpt-4o"),
}


def _build_chain() -> list[Provider]:
    chain: list[Provider] = []

    if CHUTES_API_KEY:
        chain.append(Provider(
            name="chutes_primary",
            enabled=True,
            call=_make_chutes_call(CHUTES_API_KEY),
        ))
    if CHUTES_API_KEY_FALLBACK:
        chain.append(Provider(
            name="chutes_fallback",
            enabled=True,
            call=_make_chutes_call(CHUTES_API_KEY_FALLBACK),
        ))

    openai_key = os.getenv("OPENAI_API_KEY", "")
    openai_base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    if openai_key:
        chain.append(Provider(
            name="openai",
            enabled=True,
            call=_make_openai_call(openai_key, openai_base),
            model_map=OPENAI_MODELS,
        ))

    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    if anthropic_key:
        chain.append(Provider(
            name="anthropic",
            enabled=True,
            call=_make_anthropic_call(anthropic_key),
            model_map=ANTHROPIC_MODELS,
        ))

    # Respect explicit ordering override.
    order = os.getenv("LLM_PROVIDER_CHAIN", "").strip()
    if order:
        wanted = [p.strip() for p in order.split(",") if p.strip()]
        by_name = {p.name: p for p in chain}
        chain = [by_name[n] for n in wanted if n in by_name]

    return chain


_chain: list[Provider] | None = None
_chain_lock = threading.Lock()


def get_chain() -> list[Provider]:
    global _chain
    with _chain_lock:
        if _chain is None:
            _chain = _build_chain()
        return _chain


def rebuild_chain() -> list[Provider]:
    """Force-rebuild the provider chain (e.g. after env reload)."""
    global _chain
    with _chain_lock:
        _chain = _build_chain()
        return _chain


def chain_health() -> list[dict]:
    out = []
    snap = {b["source"]: b for b in reliability.breaker_snapshot()}
    for p in get_chain():
        src = f"llm_router.{p.name}"
        b = snap.get(src, {})
        out.append({
            "name": p.name,
            "enabled": p.enabled,
            "breaker_state": b.get("state", "closed"),
            "failures": b.get("failures", 0),
            "last_error": b.get("last_error", ""),
            "remaining_cooldown_seconds": b.get("remaining_cooldown_seconds", 0.0),
        })
    return out


# ---------- public API ----------

def chat(messages, model: str, **kwargs):
    """Try each provider in the chain until one succeeds. Records the
    succeeding provider into thread-local `last_provider()` so callers can
    annotate traces. Raises the last error if every provider fails.

    Pass `retry_policy=reliability.ONE_SHOT_POLICY` to disable per-provider
    retries (useful for slow reasoning-model calls where 3× timeout is
    user-hostile)."""
    kwargs.setdefault("timeout", 30)
    retry_policy = kwargs.pop("retry_policy", reliability.DEFAULT_POLICY)
    chain = get_chain()
    if not chain:
        raise RuntimeError(
            "No LLM providers configured. Set CHUTES_API_KEY, OPENAI_API_KEY, "
            "or ANTHROPIC_API_KEY."
        )

    fallback_from: list[str] = []
    last_exc: BaseException | None = None
    t0 = time.time()

    for provider in chain:
        if not provider.enabled:
            continue
        upstream_model = provider.resolve_model(model)
        source = f"llm_router.{provider.name}"
        try:
            resp = reliability.with_retry(
                lambda p=provider, m=upstream_model: p.call(messages, m, **kwargs),
                source=source,
                policy=retry_policy,
            )
            _set_last({
                "provider": provider.name,
                "model": upstream_model,
                "latency_ms": int((time.time() - t0) * 1000),
                "fallback_from": fallback_from,
            })
            return resp
        except reliability.BreakerOpen as e:
            fallback_from.append(f"{provider.name}:breaker_open")
            last_exc = e
            continue
        except BaseException as e:
            # Classify before deciding to failover. Auth/config errors
            # mean failover won't help — both keys are usually the same
            # account, both point at the same misconfigured model — so
            # bail immediately and surface the real error.
            from .error_classifier import classify_provider_error, policy_for, ErrorClass
            cls = classify_provider_error(e)
            pol = policy_for(cls)
            fallback_from.append(f"{provider.name}:{cls.value}")
            reliability.record_error(
                source, e,
                context={"model": upstream_model,
                         "provider": provider.name,
                         "error_class": cls.value,
                         "will_fallback": pol.failover_provider},
                kind="provider_fallback" if pol.failover_provider else cls.value,
            )
            last_exc = e
            if not pol.failover_provider:
                # PROVIDER_AUTH / PROVIDER_CONFIG / SKILL_BUG-style — re-raise
                # immediately so the caller sees the real cause instead of a
                # generic "all providers failed".
                _set_last({
                    "provider": "none",
                    "model": model,
                    "latency_ms": int((time.time() - t0) * 1000),
                    "fallback_from": fallback_from,
                    "error": str(e),
                    "error_class": cls.value,
                })
                raise
            continue

    # All providers exhausted.
    _set_last({
        "provider": "none",
        "model": model,
        "latency_ms": int((time.time() - t0) * 1000),
        "fallback_from": fallback_from,
        "error": str(last_exc) if last_exc else "no providers",
    })
    if last_exc:
        raise last_exc
    raise RuntimeError("LLM router: no providers available")
