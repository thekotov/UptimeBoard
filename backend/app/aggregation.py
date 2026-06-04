"""Status aggregation: probe -> server -> service -> page.

Rules:
- A server's status is the worst status among its probes.
- A service's status is `up` if all servers are up, `down` if all are down,
  and `degraded` if some (but not all) are down/degraded.
- A page's overall status follows the same rule applied to its services.
"""

from app.models.monitoring import (
    STATUS_DEGRADED,
    STATUS_DOWN,
    STATUS_MAINTENANCE,
    STATUS_UNKNOWN,
    STATUS_UP,
)

# Ordering from best to worst for "worst wins" aggregation. Maintenance ranks
# below everything so a mixed timeline bucket is only "maintenance" when every
# sample in it was maintenance (it never overrides real up/down data).
_SEVERITY = {STATUS_MAINTENANCE: -1, STATUS_UP: 0, STATUS_UNKNOWN: 1,
             STATUS_DEGRADED: 2, STATUS_DOWN: 3}


def worst(statuses: list[str]) -> str:
    """Worst status among the given list (used for a server's probes)."""
    if not statuses:
        return STATUS_UNKNOWN
    return max(statuses, key=lambda s: _SEVERITY.get(s, 1))


def aggregate(statuses: list[str]) -> str:
    """Aggregate child statuses for a service or page.

    all up -> up; all down -> down; mix -> degraded. Unknowns are ignored
    unless every child is unknown.
    """
    if not statuses:
        return STATUS_UNKNOWN

    known = [s for s in statuses if s != STATUS_UNKNOWN]
    if not known:
        return STATUS_UNKNOWN

    if all(s == STATUS_UP for s in known):
        return STATUS_UP
    if all(s == STATUS_DOWN for s in known):
        return STATUS_DOWN
    return STATUS_DEGRADED
