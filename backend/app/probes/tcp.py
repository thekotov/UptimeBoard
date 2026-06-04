import socket
import time

from app.models.monitoring import STATUS_DOWN, STATUS_UP
from app.probes.base import ProbeOutcome, is_timeout_error


def execute(host: str, config: dict, timeout_sec: int) -> ProbeOutcome:
    """TCP connect probe.

    config keys:
      port   target TCP port (required)
    """
    port = config.get("port")
    if port is None:
        return ProbeOutcome(status=STATUS_DOWN, error="tcp probe requires a port")

    start = time.perf_counter()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout_sec):
            latency_ms = (time.perf_counter() - start) * 1000
            return ProbeOutcome(status=STATUS_UP, latency_ms=latency_ms)
    except OSError as exc:
        return ProbeOutcome(
            status=STATUS_DOWN, error=str(exc),
            kind="timeout" if is_timeout_error(exc) else None,
        )
