"""Thread-safe SSE broker: sync producers (facade/pnp threads) → async consumers.

publish() is safe to call from any thread and MUST be called only after the
facade releases InventoryApi._lock (never while holding it).
"""

from __future__ import annotations

import json
import logging
import queue
import threading

logger = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 15

_subscribers: set[queue.Queue] = set()
_sub_lock = threading.Lock()


def subscribe() -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=256)
    with _sub_lock:
        _subscribers.add(q)
    return q


def unsubscribe(q: queue.Queue) -> None:
    with _sub_lock:
        _subscribers.discard(q)


def publish(event: str, data: dict) -> None:
    with _sub_lock:
        subs = list(_subscribers)
    for q in subs:
        try:
            q.put_nowait((event, data))
        except queue.Full:
            logger.warning("SSE subscriber queue full; dropping %s", event)


def format_frame(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
