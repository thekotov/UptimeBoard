"""Probe executors with a common interface.

Each executor takes a host and the probe's config dict and returns a
:class:`ProbeOutcome`. Executors are synchronous so they can run inside the
APScheduler worker thread pool without an event loop.
"""

import socket
from dataclasses import dataclass

from app.models.monitoring import STATUS_DOWN, STATUS_UP


@dataclass
class ProbeOutcome:
    status: str
    latency_ms: float | None = None
    error: str | None = None
    # Failure classification, used to soften transient/slow failures. "timeout"
    # marks a no-response-in-time failure, which the runner renders as "degraded"
    # (slow ≠ hard outage) rather than "down". None for other outcomes.
    kind: str | None = None
    # Optional probe-specific metadata, persisted by the runner. The TLS probe
    # fills this with certificate info: {expires_at, not_before, issuer, subject,
    # sans}. None for probes that carry no metadata.
    meta: dict | None = None


def is_timeout_error(exc: BaseException) -> bool:
    """True if an exception represents a network timeout (vs. a hard failure such
    as connection refused, DNS error, TLS error). Covers stdlib socket timeouts
    and httpx's ConnectTimeout/ReadTimeout/PoolTimeout (matched by name so we don't
    import httpx here)."""
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    return "timeout" in type(exc).__name__.lower()


def run_probe(probe_type: str, host: str, config: dict, timeout_sec: int) -> ProbeOutcome:
    """Dispatch to the executor matching ``probe_type``."""
    from app.probes import http as http_probe
    from app.probes import icmp as icmp_probe
    from app.probes import tcp as tcp_probe
    from app.probes import tls as tls_probe

    try:
        if probe_type == "http":
            return http_probe.execute(host, config, timeout_sec)
        if probe_type == "tcp":
            return tcp_probe.execute(host, config, timeout_sec)
        if probe_type == "icmp":
            return icmp_probe.execute(host, config, timeout_sec)
        if probe_type == "tls":
            return tls_probe.execute(host, config, timeout_sec)
        return ProbeOutcome(status=STATUS_DOWN, error=f"unknown probe type: {probe_type}")
    except Exception as exc:  # noqa: BLE001 - any executor failure is a down result
        return ProbeOutcome(
            status=STATUS_DOWN, error=str(exc),
            kind="timeout" if is_timeout_error(exc) else None,
        )


__all__ = ["ProbeOutcome", "run_probe", "is_timeout_error", "STATUS_UP", "STATUS_DOWN"]
