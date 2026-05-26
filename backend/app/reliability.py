"""Reliability primitives — retry, timeout, error classification, error logging.

Used everywhere the platform calls an LLM, an external HTTP API, or any other
fallible operation. The goal is a single place that knows what's retryable and
what isn't, so individual modules don't reinvent (or skip) the policy.

Retry policy:
  - Retry on: 408, 425, 429, 500, 502, 503, 504, timeouts, connection errors.
  - Do NOT retry on: 4xx auth (401/403), 400 bad request, validation errors.
  - Honor `Retry-After` on 429/503 when the upstream provides it.
  - Exponential backoff with jitter, capped at `max_delay`.
"""
from __future__ import annotations

import random
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


# ---------- error classification ----------

_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
_AUTH_STATUS = {401, 403}


def _status_code(exc: BaseException) -> int | None:
    """Best-effort extract HTTP status code from common exception shapes."""
    for attr in ("status_code", "http_status", "code"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    resp = getattr(exc, "response", None)
    if resp is not None:
        v = getattr(resp, "status_code", None)
        if isinstance(v, int):
            return v
    return None


def is_retryable(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    if any(k in name for k in ("timeout", "connection", "readtimeout")):
        return True
    sc = _status_code(exc)
    if sc in _RETRYABLE_STATUS:
        return True
    # Heuristic: OpenAI SDK wraps with classes like RateLimitError, APIConnectionError
    if any(k in name for k in ("ratelimit", "apiconnection", "apitimeout",
                               "serviceunavailable", "internalserver")):
        return True
    return False


def is_auth_error(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    if "auth" in name or "permission" in name:
        return True
    if _status_code(exc) in _AUTH_STATUS:
        return True
    return False


def retry_after_seconds(exc: BaseException) -> float | None:
    resp = getattr(exc, "response", None)
    if resp is None:
        return None
    try:
        headers = getattr(resp, "headers", None) or {}
        v = headers.get("Retry-After") or headers.get("retry-after")
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


# ---------- retry executor ----------

@dataclass
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 0.5    # seconds
    max_delay: float = 8.0
    jitter: float = 0.25       # +/- this fraction of base_delay


DEFAULT_POLICY = RetryPolicy()


# ---------- circuit breaker ----------

@dataclass
class BreakerConfig:
    failure_threshold: int = 5       # consecutive failures before opening
    cooldown_seconds: float = 30.0   # how long the breaker stays open
    half_open_max_calls: int = 1     # probe attempts allowed in half-open


@dataclass
class _BreakerState:
    failures: int = 0
    opened_at: float = 0.0
    state: str = "closed"            # 'closed' | 'open' | 'half_open'
    last_error: str = ""
    half_open_in_flight: int = 0


class BreakerOpen(Exception):
    """Raised when a call is short-circuited because its breaker is open."""
    def __init__(self, source: str, remaining_cooldown: float):
        super().__init__(f"circuit breaker open for {source}; "
                         f"retry in {remaining_cooldown:.1f}s")
        self.source = source
        self.remaining_cooldown = remaining_cooldown


# Breaker state is now persisted to SQLite (db.breaker_states) so multiple
# uvicorn workers and process restarts see the same view. The in-memory
# lock below only serializes admit/record_outcome inside this process —
# SQLite's busy_timeout handles cross-process serialization at the file level.
_breaker_lock = threading.Lock()
_breaker_cfg: dict[str, BreakerConfig] = {}
_default_breaker_cfg = BreakerConfig()


def configure_breaker(source: str, cfg: BreakerConfig) -> None:
    with _breaker_lock:
        _breaker_cfg[source] = cfg


def _cfg(source: str) -> BreakerConfig:
    return _breaker_cfg.get(source, _default_breaker_cfg)


def _admit(source: str) -> None:
    """Raise BreakerOpen if the breaker for `source` rejects this call now.
    State is read from and written to db.breaker_states under a per-process
    lock so concurrent calls in the same worker don't race; cross-worker
    races are bounded by the cooldown window which is much longer than the
    transaction window."""
    cfg = _cfg(source)
    from . import db
    with _breaker_lock:
        st = db.get_breaker_state(source)
        now_epoch = time.time()
        if st["state"] == "open":
            elapsed = now_epoch - st["opened_at_epoch"]
            if elapsed >= cfg.cooldown_seconds:
                # Cooldown elapsed — transition to half-open and let this call probe.
                db.save_breaker_state(source, state="half_open",
                                      half_open_in_flight=1)
            else:
                raise BreakerOpen(source, cfg.cooldown_seconds - elapsed)
        elif st["state"] == "half_open":
            if st["half_open_in_flight"] >= cfg.half_open_max_calls:
                raise BreakerOpen(source, 0.0)
            db.save_breaker_state(source,
                                  half_open_in_flight=st["half_open_in_flight"] + 1)


def _record_outcome(source: str, ok: bool, err: BaseException | None = None) -> None:
    cfg = _cfg(source)
    from . import db
    with _breaker_lock:
        st = db.get_breaker_state(source)
        if ok:
            db.save_breaker_state(source, failures=0, state="closed",
                                  half_open_in_flight=0, last_error="")
            return
        last_error = f"{type(err).__name__}: {err}"[:200] if err else ""
        if err is not None and not (is_retryable(err) or is_auth_error(err)):
            # Only update the message so operators can see context, but
            # don't count it toward the failure threshold.
            db.save_breaker_state(source, last_error=last_error)
            return
        new_failures = st["failures"] + 1
        if st["state"] == "half_open":
            # A failure during half-open re-opens immediately.
            db.save_breaker_state(source, failures=new_failures, state="open",
                                  opened_at_epoch=time.time(),
                                  half_open_in_flight=0,
                                  last_error=last_error)
            return
        if new_failures >= cfg.failure_threshold:
            db.save_breaker_state(source, failures=new_failures, state="open",
                                  opened_at_epoch=time.time(),
                                  last_error=last_error)
        else:
            db.save_breaker_state(source, failures=new_failures,
                                  last_error=last_error)


def breaker_snapshot() -> list[dict]:
    from . import db
    now_epoch = time.time()
    out = []
    for st in db.list_breaker_states():
        cfg = _cfg(st["source"])
        remaining = 0.0
        if st["state"] == "open":
            remaining = max(0.0, cfg.cooldown_seconds - (now_epoch - st["opened_at_epoch"]))
        out.append({
            "source": st["source"],
            "state": st["state"],
            "failures": st["failures"],
            "remaining_cooldown_seconds": round(remaining, 2),
            "last_error": st["last_error"],
            "updated_at": st.get("updated_at"),
            "failure_threshold": cfg.failure_threshold,
            "cooldown_seconds": cfg.cooldown_seconds,
        })
    return out


def reset_breaker(source: str | None = None) -> None:
    from . import db
    db.delete_breaker_state(source)


def with_retry(fn: Callable[[], Any], *, policy: RetryPolicy = DEFAULT_POLICY,
               source: str = "unknown",
               use_breaker: bool = True,
               on_error: Callable[[BaseException, int], None] | None = None) -> Any:
    """Execute fn() with retry on retryable errors. Re-raises final exception.

    If `use_breaker` is True (default), the call is gated by a per-source
    circuit breaker — repeated failures open the breaker and subsequent calls
    fail-fast with `BreakerOpen` until the cooldown elapses.
    """
    last: BaseException | None = None
    for attempt in range(1, policy.max_attempts + 1):
        if use_breaker:
            _admit(source)  # raises BreakerOpen if rejected
        try:
            out = fn()
            if use_breaker:
                _record_outcome(source, ok=True)
            return out
        except BreakerOpen:
            raise
        except BaseException as e:
            last = e
            if use_breaker:
                _record_outcome(source, ok=False, err=e)
            if on_error is not None:
                try:
                    on_error(e, attempt)
                except Exception:
                    pass
            if attempt >= policy.max_attempts or not is_retryable(e):
                raise
            wait = retry_after_seconds(e)
            if wait is None:
                wait = min(
                    policy.max_delay,
                    policy.base_delay * (2 ** (attempt - 1))
                    + random.uniform(-policy.jitter, policy.jitter) * policy.base_delay,
                )
                wait = max(0.0, wait)
            time.sleep(wait)
    assert last is not None
    raise last


# ---------- error logging ----------

def record_error(source: str, exc: BaseException,
                 context: dict | None = None,
                 kind: str | None = None) -> int | None:
    """Persist an error row. Best-effort: failures here must never escape."""
    try:
        from . import db
        return db.record_error(
            source=source,
            kind=kind or type(exc).__name__,
            message=str(exc)[:500],
            context=context or {},
            traceback_text=traceback.format_exc()[-2000:],
        )
    except Exception:
        return None
