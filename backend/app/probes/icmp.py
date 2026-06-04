from icmplib import ping

from app.models.monitoring import STATUS_DEGRADED, STATUS_DOWN, STATUS_UP
from app.probes.base import ProbeOutcome


def execute(host: str, config: dict, timeout_sec: int) -> ProbeOutcome:
    """ICMP echo probe via icmplib.

    config keys:
      count           number of echo requests per series (default 3)
      packet_size     payload size in bytes (default 56)
      loss_threshold  fraction of loss (0..1) tolerated before degrading (default 0)

    icmplib uses raw sockets when the process has CAP_NET_RAW (the worker
    container is granted it); otherwise it transparently falls back to
    unprivileged datagram sockets where the OS permits.

    Single dropped packets are common on virtualised / NAT'd network paths
    (Docker Desktop, cloud VMs) and rarely mean the host is actually degraded.
    So when a series shows loss we run one confirmation series and report
    degraded only if the loss persists — this kills transient false-positive
    "packet loss" blips while still catching sustained loss.
    """
    count = int(config.get("count", 3))
    packet_size = int(config.get("packet_size", 56))
    loss_threshold = float(config.get("loss_threshold", 0) or 0)

    def series():
        return ping(
            host,
            count=count,
            interval=0.2,
            timeout=timeout_sec,
            payload_size=packet_size,
            privileged=True,
        )

    result = series()
    if not result.is_alive:
        return ProbeOutcome(status=STATUS_DOWN, error="host unreachable (no replies)")

    # Within tolerance -> healthy, no need to re-probe.
    if result.packet_loss <= loss_threshold:
        return ProbeOutcome(status=STATUS_UP, latency_ms=result.avg_rtt)

    # Loss seen -> confirm with a second series before flagging degraded.
    # Keep the best (lowest-loss) of the two samples so a single transient blip
    # in either series doesn't drag the verdict down.
    confirm = series()
    if confirm.is_alive and confirm.packet_loss < result.packet_loss:
        result = confirm

    if result.packet_loss <= loss_threshold:
        return ProbeOutcome(status=STATUS_UP, latency_ms=result.avg_rtt)

    return ProbeOutcome(
        status=STATUS_DEGRADED,
        latency_ms=result.avg_rtt,
        error=f"packet loss {result.packet_loss * 100:.0f}%",
    )
