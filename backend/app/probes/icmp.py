from icmplib import ping

from app.models.monitoring import STATUS_DEGRADED, STATUS_DOWN, STATUS_UP
from app.probes.base import ProbeOutcome


def execute(host: str, config: dict, timeout_sec: int) -> ProbeOutcome:
    """ICMP echo probe via icmplib.

    config keys:
      count        number of echo requests (default 3)
      packet_size  payload size in bytes (default 56)

    icmplib uses raw sockets when the process has CAP_NET_RAW (the worker
    container is granted it); otherwise it transparently falls back to
    unprivileged datagram sockets where the OS permits.
    """
    count = int(config.get("count", 3))
    packet_size = int(config.get("packet_size", 56))

    host_result = ping(
        host,
        count=count,
        interval=0.2,
        timeout=timeout_sec,
        payload_size=packet_size,
        privileged=True,
    )

    if not host_result.is_alive:
        return ProbeOutcome(status=STATUS_DOWN, error="host unreachable (no replies)")

    latency_ms = host_result.avg_rtt
    # Partial packet loss -> degraded rather than fully up.
    if host_result.packet_loss > 0:
        return ProbeOutcome(
            status=STATUS_DEGRADED,
            latency_ms=latency_ms,
            error=f"packet loss {host_result.packet_loss * 100:.0f}%",
        )
    return ProbeOutcome(status=STATUS_UP, latency_ms=latency_ms)
