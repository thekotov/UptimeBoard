import socket
import ssl
import time
from datetime import datetime, timezone

from app.models.monitoring import STATUS_DEGRADED, STATUS_DOWN, STATUS_UP
from app.probes.base import ProbeOutcome, is_timeout_error


def execute(host: str, config: dict, timeout_sec: int) -> ProbeOutcome:
    """TLS certificate probe — checks reachability and certificate expiry.

    config keys:
      port        TLS port (default 443)
      warn_days   mark "degraded" when fewer than this many days remain (default 14)
    """
    port = int(config.get("port", 443))
    warn_days = int(config.get("warn_days", 14))

    ctx = ssl.create_default_context()
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout_sec) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
        latency_ms = (time.perf_counter() - start) * 1000
    except (OSError, ssl.SSLError) as exc:
        return ProbeOutcome(
            status=STATUS_DOWN, error=str(exc),
            kind="timeout" if is_timeout_error(exc) else None,
        )

    not_after = cert.get("notAfter")
    if not not_after:
        return ProbeOutcome(status=STATUS_UP, latency_ms=latency_ms)

    expires = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    days_left = (expires - datetime.now(timezone.utc)).days

    if days_left < 0:
        return ProbeOutcome(status=STATUS_DOWN, latency_ms=latency_ms,
                            error=f"certificate expired {-days_left}d ago")
    if days_left < warn_days:
        return ProbeOutcome(status=STATUS_DEGRADED, latency_ms=latency_ms,
                            error=f"certificate expires in {days_left}d")
    return ProbeOutcome(status=STATUS_UP, latency_ms=latency_ms)
