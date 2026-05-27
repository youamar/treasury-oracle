"""In-process pub/sub for live agent trace events.

The agent persists every trace event to SQLite via `db.append_trace`. This
module sits on top so subscribers (SSE endpoint) get the same events pushed
in real time instead of polling the DB.

Design constraints honored:
  * Single-process only — fine for the hackathon deployment (one uvicorn
    worker). For multi-worker we'd need Redis pub/sub, out of scope.
  * Non-blocking — `publish()` never raises and never waits. If a slow
    subscriber falls behind, its queue drops events past `MAX_BUFFER`.
  * Auto-cleanup — when a session finishes (publish_done) subscribers are
    notified and the queue is dropped after a short grace period so the
    in-memory map doesn't grow.
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

# Per-session subscriber queues. Each subscriber gets its own queue so a
# slow consumer can't backpressure a fast one.
_subscribers: dict[str, list[asyncio.Queue]] = {}
_lock = threading.Lock()

# Bound per-subscriber buffer so a frontend that opens an SSE stream and
# disappears doesn't grow memory forever. Old events get dropped if the
# subscriber falls > MAX_BUFFER behind.
MAX_BUFFER = 500


def subscribe(session_id: str) -> asyncio.Queue:
    """Create a new subscriber queue for `session_id`. Caller must call
    `unsubscribe(session_id, queue)` when done."""
    q: asyncio.Queue = asyncio.Queue(maxsize=MAX_BUFFER)
    with _lock:
        _subscribers.setdefault(session_id, []).append(q)
    return q


def unsubscribe(session_id: str, q: asyncio.Queue) -> None:
    with _lock:
        bucket = _subscribers.get(session_id, [])
        if q in bucket:
            bucket.remove(q)
        if not bucket:
            _subscribers.pop(session_id, None)


def publish(session_id: str, event: dict[str, Any]) -> None:
    """Push an event to every subscriber of `session_id`. Safe to call from
    any thread — sync code from db.append_trace, async code from the SSE
    handler, etc. Never raises and never blocks."""
    with _lock:
        bucket = list(_subscribers.get(session_id, []))
    if not bucket:
        return  # no subscribers — drop on the floor, no work done
    for q in bucket:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            # Subscriber is too slow. Drop oldest, push newest — better than
            # holding back the publisher.
            try:
                _ = q.get_nowait()
                q.put_nowait(event)
            except Exception:
                pass


def publish_done(session_id: str, payload: dict[str, Any] | None = None) -> None:
    """Signal end-of-stream — subscribers see a `session_complete` event and
    can close their connection. Queues are dropped after a short grace
    period so any still-draining consumer gets the final events."""
    publish(session_id, {"type": "session_complete", "payload": payload or {}})
    # Schedule cleanup off the publisher thread.
    def _cleanup():
        time.sleep(2.0)
        with _lock:
            _subscribers.pop(session_id, None)
    threading.Thread(target=_cleanup, daemon=True).start()


def subscriber_count(session_id: str) -> int:
    with _lock:
        return len(_subscribers.get(session_id, []))
