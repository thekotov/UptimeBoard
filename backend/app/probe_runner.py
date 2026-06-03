"""Shared probe execution: run a probe, store the result + denormalised status,
manage incidents (flap guard, maintenance suppression, repeat reminders, alert
grouping and escalation), and publish a real-time update. Used by both the
scheduler worker and the admin "check now" endpoint."""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.alerts import all_escalation_channels, base_channels, dispatch, escalation_channels
from app.config import settings
from app.models import Incident, MaintenanceWindow, Probe, ProbeResult
from app.models.monitoring import STATUS_DEGRADED, STATUS_DOWN, STATUS_UNKNOWN, STATUS_UP
from app.probes import ProbeOutcome, run_probe
from app.realtime import publish_status_update
from app.status_service import find_page_for_probe

logger = logging.getLogger("probe_runner")


def _is_bad(status: str) -> bool:
    return status != STATUS_UP and status != STATUS_UNKNOWN


def in_maintenance(db: Session, page_id: int, now: datetime) -> bool:
    return (
        db.query(MaintenanceWindow.id)
        .filter(
            MaintenanceWindow.page_id == page_id,
            MaintenanceWindow.starts_at <= now,
            MaintenanceWindow.ends_at >= now,
        )
        .first()
        is not None
    )


def _apply_latency_threshold(probe: Probe, outcome: ProbeOutcome) -> ProbeOutcome:
    if (
        outcome.status == STATUS_UP
        and probe.latency_degraded_ms
        and outcome.latency_ms is not None
        and outcome.latency_ms > probe.latency_degraded_ms
    ):
        return ProbeOutcome(
            status=STATUS_DEGRADED,
            latency_ms=outcome.latency_ms,
            error=f"latency {outcome.latency_ms:.0f}ms > {probe.latency_degraded_ms}ms",
        )
    return outcome


def _evaluate_heartbeat(probe: Probe, now: datetime) -> ProbeOutcome:
    """A heartbeat probe is up while pings arrive within interval + grace."""
    grace = int((probe.config or {}).get("grace_sec", 0))
    if probe.last_ping_at is None:
        return ProbeOutcome(status=STATUS_UNKNOWN, error="no ping received yet")
    age = (now - probe.last_ping_at).total_seconds()
    if age <= probe.interval_sec + grace:
        return ProbeOutcome(status=STATUS_UP, latency_ms=None)
    return ProbeOutcome(status=STATUS_DOWN, error=f"no ping for {int(age)}s")


def handle_incident(db: Session, probe: Probe, outcome: ProbeOutcome, host: str,
                    page_id: int | None, maint: bool, now: datetime) -> None:
    open_incident = (
        db.query(Incident)
        .filter(Incident.probe_id == probe.id, Incident.resolved_at.is_(None))
        .first()
    )
    bad = _is_bad(outcome.status)
    threshold = max(1, probe.failure_threshold)
    common = dict(probe_name=probe.name, server_host=host, status=outcome.status,
                  error=outcome.error, server_id=probe.server_id)

    if bad:
        if open_incident is None:
            if probe.consecutive_failures >= threshold and not maint:
                db.add(Incident(probe_id=probe.id, last_status=outcome.status, last_notified_at=now))
                dispatch(base_channels(db, page_id), event="opened", **common)
        else:
            open_incident.last_status = outcome.status
            if maint:
                return
            # Acknowledged incidents stay open but go quiet (no repeat/escalation).
            if open_incident.acknowledged_at is not None:
                return
            age_min = (now - open_incident.started_at).total_seconds() / 60
            # repeat reminders to base channels
            repeat = settings.alert_repeat_min
            if repeat > 0:
                last = open_incident.last_notified_at or open_incident.started_at
                if now - last >= timedelta(minutes=repeat):
                    dispatch(base_channels(db, page_id), event="ongoing", **common)
                    open_incident.last_notified_at = now
            # escalate once to escalation channels whose threshold has elapsed
            if open_incident.escalated_at is None:
                esc = escalation_channels(db, page_id, int(age_min))
                if esc:
                    dispatch(esc, event="escalated", **common)
                    open_incident.escalated_at = now
    elif open_incident is not None:
        open_incident.resolved_at = now
        if not maint:
            dispatch(base_channels(db, page_id), event="resolved", **common)
            if open_incident.escalated_at is not None:
                dispatch(all_escalation_channels(db, page_id), event="resolved", group=False, **common)


def execute_and_store(db: Session, probe: Probe) -> ProbeOutcome:
    """Run one probe check, persist result + denormalised status + incident, publish."""
    host = probe.server.host
    now = datetime.now(timezone.utc)

    if probe.type == "heartbeat":
        outcome = _evaluate_heartbeat(probe, now)
    else:
        outcome = _apply_latency_threshold(
            probe, run_probe(probe.type, host, probe.config or {}, probe.timeout_sec)
        )

    db.add(
        ProbeResult(
            probe_id=probe.id, status=outcome.status, latency_ms=outcome.latency_ms,
            error=outcome.error, checked_at=now,
        )
    )
    probe.last_status = outcome.status
    probe.last_latency_ms = outcome.latency_ms
    probe.last_checked_at = now
    probe.last_error = outcome.error
    probe.consecutive_failures = probe.consecutive_failures + 1 if _is_bad(outcome.status) else 0
    db.flush()

    page = find_page_for_probe(db, probe.id)
    maint = page is not None and in_maintenance(db, page.id, now)
    handle_incident(db, probe, outcome, host, page.id if page else None, maint, now)
    db.commit()

    if page is not None:
        publish_status_update(page.slug, {"probe_id": probe.id, "status": outcome.status})

    logger.info(
        "probe %s (%s %s) -> %s%s%s",
        probe.id, probe.type, host, outcome.status,
        f" ({outcome.error})" if outcome.error else "",
        " [maintenance]" if maint else "",
    )
    return outcome
