"""Login rate limiter (per key, fixed window).

Backed by Redis so limits are shared across API processes/replicas; falls back
to an in-memory counter if Redis is unavailable. Protects the login endpoint
from brute force.
"""

import logging
import threading
import time

from app.realtime import get_sync_redis

logger = logging.getLogger("ratelimit")

_MAX_FAILURES = 5
_WINDOW_SEC = 300  # 5 minutes

_lock = threading.Lock()
_failures: dict[str, list[float]] = {}


def _rkey(key: str) -> str:
    return f"login:fail:{key}"


def _mem_prune(key: str, now: float) -> None:
    items = [t for t in _failures.get(key, []) if now - t < _WINDOW_SEC]
    if items:
        _failures[key] = items
    else:
        _failures.pop(key, None)


def is_locked(key: str) -> bool:
    try:
        val = get_sync_redis().get(_rkey(key))
        return int(val) >= _MAX_FAILURES if val is not None else False
    except Exception as exc:  # noqa: BLE001 — Redis down: degrade to in-memory
        logger.warning("ratelimit Redis read failed, using in-memory: %s", exc)
        now = time.monotonic()
        with _lock:
            _mem_prune(key, now)
            return len(_failures.get(key, [])) >= _MAX_FAILURES


def record_failure(key: str) -> None:
    try:
        r = get_sync_redis()
        # INCR then arm the window expiry on first failure — fixed-window counter.
        if r.incr(_rkey(key)) == 1:
            r.expire(_rkey(key), _WINDOW_SEC)
        return
    except Exception as exc:  # noqa: BLE001
        logger.warning("ratelimit Redis write failed, using in-memory: %s", exc)
        now = time.monotonic()
        with _lock:
            _failures.setdefault(key, []).append(now)
            _mem_prune(key, now)


def reset(key: str) -> None:
    try:
        get_sync_redis().delete(_rkey(key))
    except Exception:  # noqa: BLE001
        pass
    with _lock:
        _failures.pop(key, None)
