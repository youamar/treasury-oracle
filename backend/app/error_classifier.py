"""Single source of truth for "what kind of failure is this?"

The system has many places that catch exceptions — the LLM router, the
agent's tool dispatcher, the one-shot text endpoints. Without a shared
classifier, each site invents its own rules (or worse: treats everything
the same and retries blindly). That wastes LLM tokens on errors the LLM
can't fix, and silently hides bugs that should page an operator.

A classification answers three questions:
  * Should we retry the same call? (transient vs. permanent)
  * Should we failover to a different provider?
  * Is this the LLM's fault, or ours/the provider's?

`classify(exc)` walks an exception and returns one of `ErrorClass`. Each
class has a fixed policy via the `policy_for(cls)` helper — callers don't
hand-roll if/else.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ErrorClass(str, Enum):
    # --- transport / infra ---
    CONNECTION       = "connection"        # DNS, TCP reset, socket — usually transient
    PROVIDER_DOWN    = "provider_down"     # 5xx, gateway timeout — provider's problem
    PROVIDER_AUTH    = "provider_auth"     # 401/403 — our key is wrong, retrying won't help
    PROVIDER_CONFIG  = "provider_config"   # 404 model not found, 400 bad request — our config bug
    MODEL_RATE_LIMIT = "model_rate_limit"  # 429 — back off, then failover

    # --- LLM-side ---
    LLM_OUTPUT_INVALID = "llm_output_invalid"  # malformed JSON, empty content after retries
    LLM_TOOL_MISUSE    = "llm_tool_misuse"     # LLM called tool with wrong arg types/shape

    # --- our side ---
    SKILL_BUG     = "skill_bug"            # exception inside skill handler — code bug, not LLM's fault
    DATA_PROBLEM  = "data_problem"         # input was unprocessable (bad OCR, missing fields)

    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ErrorPolicy:
    """How callers should react to a given error class."""
    retry_same_provider: bool   # try this provider again (transient transport hiccup)
    failover_provider:   bool   # try a different provider (this one is sick)
    bill_llm_for_recovery: bool # OK to ask the LLM to fix this? (False for our-side bugs)
    surface_to_operator:  bool  # warrants a loud log + alert path
    user_facing_message: str    # short, plain explanation for the UI / trace


_POLICIES: dict[ErrorClass, ErrorPolicy] = {
    ErrorClass.CONNECTION: ErrorPolicy(
        retry_same_provider=True,  failover_provider=True,
        bill_llm_for_recovery=False, surface_to_operator=False,
        user_facing_message="Temporary network hiccup — retrying.",
    ),
    ErrorClass.PROVIDER_DOWN: ErrorPolicy(
        # 5xx can be a transient hiccup (restart, brief overload) — give
        # the same provider one quick retry before falling over.
        retry_same_provider=True,  failover_provider=True,
        bill_llm_for_recovery=False, surface_to_operator=True,
        user_facing_message="LLM provider is unavailable — switching to backup.",
    ),
    ErrorClass.PROVIDER_AUTH: ErrorPolicy(
        retry_same_provider=False, failover_provider=False,
        bill_llm_for_recovery=False, surface_to_operator=True,
        user_facing_message="LLM credentials rejected — check API key.",
    ),
    ErrorClass.PROVIDER_CONFIG: ErrorPolicy(
        retry_same_provider=False, failover_provider=False,
        bill_llm_for_recovery=False, surface_to_operator=True,
        user_facing_message="LLM configuration error (e.g. model name wrong) — operator must fix.",
    ),
    ErrorClass.MODEL_RATE_LIMIT: ErrorPolicy(
        # Rate-limit window resets fast — honor Retry-After then retry.
        # If still limited after the policy's max_attempts, fall over.
        retry_same_provider=True,  failover_provider=True,
        bill_llm_for_recovery=False, surface_to_operator=False,
        user_facing_message="LLM rate-limited — switching to backup.",
    ),
    ErrorClass.LLM_OUTPUT_INVALID: ErrorPolicy(
        retry_same_provider=False, failover_provider=False,
        bill_llm_for_recovery=True,  surface_to_operator=False,
        user_facing_message="LLM gave an invalid response — asking it to try again.",
    ),
    ErrorClass.LLM_TOOL_MISUSE: ErrorPolicy(
        retry_same_provider=False, failover_provider=False,
        bill_llm_for_recovery=True,  surface_to_operator=False,
        user_facing_message="LLM called a tool incorrectly — coaching it to retry.",
    ),
    ErrorClass.SKILL_BUG: ErrorPolicy(
        retry_same_provider=False, failover_provider=False,
        bill_llm_for_recovery=False, surface_to_operator=True,
        user_facing_message="A platform tool failed (not the LLM's fault) — tool will be disabled for this session.",
    ),
    ErrorClass.DATA_PROBLEM: ErrorPolicy(
        retry_same_provider=False, failover_provider=False,
        bill_llm_for_recovery=False, surface_to_operator=False,
        user_facing_message="Input data couldn't be processed — needs human review.",
    ),
    ErrorClass.UNKNOWN: ErrorPolicy(
        retry_same_provider=False, failover_provider=True,
        bill_llm_for_recovery=False, surface_to_operator=True,
        user_facing_message="Unexpected error — see logs.",
    ),
}


def policy_for(cls: ErrorClass) -> ErrorPolicy:
    return _POLICIES[cls]


# --- classifiers --------------------------------------------------------

_PROVIDER_5XX = {500, 502, 503, 504}
_PROVIDER_4XX_CONFIG = {400, 404, 405, 415, 422}


def _status_code(exc: BaseException) -> int | None:
    """Best-effort HTTP status extraction — OpenAI SDK errors carry it on
    `.status_code` or `.response.status_code`."""
    sc = getattr(exc, "status_code", None)
    if sc is None:
        resp = getattr(exc, "response", None)
        sc = getattr(resp, "status_code", None) if resp is not None else None
    try:
        return int(sc) if sc is not None else None
    except (TypeError, ValueError):
        return None


def classify_provider_error(exc: BaseException) -> ErrorClass:
    """Classify an exception raised by an LLM provider call (the request
    side — what came back from OpenAI/Chutes/Anthropic). Use for retry +
    failover decisions in the router."""
    name = type(exc).__name__.lower()
    sc = _status_code(exc)

    # 1) HTTP status first — most reliable signal.
    if sc is not None:
        if sc in (401, 403):
            return ErrorClass.PROVIDER_AUTH
        if sc == 429:
            return ErrorClass.MODEL_RATE_LIMIT
        if sc in _PROVIDER_5XX:
            return ErrorClass.PROVIDER_DOWN
        if sc in _PROVIDER_4XX_CONFIG:
            return ErrorClass.PROVIDER_CONFIG

    # 2) Exception class names — OpenAI SDK uses descriptive types.
    if "auth" in name or "permission" in name:
        return ErrorClass.PROVIDER_AUTH
    if "ratelimit" in name:
        return ErrorClass.MODEL_RATE_LIMIT
    if "notfound" in name or "badrequest" in name:
        return ErrorClass.PROVIDER_CONFIG
    if any(k in name for k in ("timeout", "readtimeout", "apitimeout")):
        return ErrorClass.CONNECTION
    if any(k in name for k in ("connection", "apiconnection", "remotedisconnected",
                               "remoteprotocol")):
        return ErrorClass.CONNECTION
    if any(k in name for k in ("serviceunavailable", "internalserver",
                               "badgateway", "gatewaytimeout")):
        return ErrorClass.PROVIDER_DOWN

    return ErrorClass.UNKNOWN


def classify_skill_error(exc: BaseException) -> ErrorClass:
    """Classify an exception raised by a skill / tool handler.

    A `TypeError` means the LLM passed the wrong arg shape — that's a
    coaching opportunity, the LLM can fix it on the next turn.

    Anything else from inside a skill is OUR bug — KeyError because the
    skill assumes a config key, IndexError, ZeroDivisionError, ValueError
    from bad math, etc. The LLM cannot fix our code by trying again, so
    we MUST NOT bill it tokens to retry."""
    if isinstance(exc, TypeError):
        return ErrorClass.LLM_TOOL_MISUSE
    return ErrorClass.SKILL_BUG


def classify_output_problem(reason: str) -> ErrorClass:
    """Classify a non-exception failure where the LLM produced output we
    couldn't use (empty content, malformed JSON, schema violation).

    `reason` is a short string for the caller's logs — we don't currently
    branch on it but it's recorded for diagnosis."""
    _ = reason
    return ErrorClass.LLM_OUTPUT_INVALID


__all__ = [
    "ErrorClass",
    "ErrorPolicy",
    "policy_for",
    "classify_provider_error",
    "classify_skill_error",
    "classify_output_problem",
]
