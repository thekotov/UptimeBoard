"""Very small in-memory login rate limiter (per key, sliding window).

Good enough for a single-process deployment; for multi-process, move the
counters to Redis. Protects the login endpoint from brute force.
"""

import threading
import time

_MAX_FAILURES = 5
_WINDOW_SEC = 300  # 5 minutes

_lock = threading.Lock()
_failures: dict[str, list[float]] = {}


def _prune(key: str, now: float) -> None:
    items = [t for t in _failures.get(key, []) if now - t < _WINDOW_SEC]
    if items:
        _failures[key] = items
    else:
        _failures.pop(key, None)


def is_locked(key: str) -> bool:
    now = time.monotonic()
    with _lock:
        _prune(key, now)
        return len(_failures.get(key, [])) >= _MAX_FAILURES


def record_failure(key: str) -> None:
    now = time.monotonic()
    with _lock:
        _failures.setdefault(key, []).append(now)
        _prune(key, now)


def reset(key: str) -> None:
    with _lock:
        _failures.pop(key, None)
