"""Uplink self-check (anti-storm gate).

A single-worker monitor is itself a single point of network failure: if the
worker's own connectivity (or DNS) blips, *every* probe fails at once and the
whole dashboard alarms — a flood of false "down"s that don't reflect the targets.

Before trusting a failed check, the runner asks :func:`uplink_ok`, which verifies
the worker can still reach the wider internet by TCP-connecting to a few stable
anchor hosts. The result is cached for a few seconds so it costs almost nothing
per check, and it is only consulted when a probe already looks bad.

Fail-open by design: if the feature is disabled, no anchors are configured, or
anything is misconfigured, we report the uplink as healthy so monitoring keeps
working as before.
"""

import socket
import threading
import time

from app.config import settings

_lock = threading.Lock()
# Cached verdict: (ok, monotonic_timestamp). Start optimistic.
_cache: tuple[bool, float] = (True, 0.0)


def _anchors() -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for token in (settings.uplink_anchors or "").split(","):
        token = token.strip()
        if not token:
            continue
        host, _, port = token.partition(":")
        host = host.strip()
        if not host:
            continue
        try:
            out.append((host, int(port) if port else 443))
        except ValueError:
            continue
    return out


def _anchor_reachable(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _measure() -> bool:
    """Uplink is healthy if *any* anchor answers (so one dead anchor doesn't make
    us think we're offline). Short-circuits on the first success, so the slow
    all-anchors path only runs when we're genuinely cut off."""
    anchors = _anchors()
    if not anchors:
        return True
    return any(_anchor_reachable(h, p, settings.uplink_timeout_sec) for h, p in anchors)


def uplink_ok() -> bool:
    """Whether the worker currently has working network. Cached for
    ``uplink_cache_sec`` so it's cheap to call on every failed check."""
    if not settings.uplink_check_enabled:
        return True
    now = time.monotonic()
    ok, at = _cache
    if now - at < settings.uplink_cache_sec:
        return ok
    ok = _measure()
    with _lock:
        globals()["_cache"] = (ok, time.monotonic())
    return ok
