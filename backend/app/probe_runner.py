"""Shared probe execution: run a probe, store the result + denormalised status,
manage incidents (flap guard, maintenance suppression, repeat reminders, alert
grouping and escalation), and publish a real-time update. Used by both the
scheduler worker and the admin "check now" endpoint."""

import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.alerts import (
    all_escalation_channels,
    base_channels,
    dispatch,
    escalation_channels,
    get_alert_storm_window_sec,
    queue_storm_alert,
)
from app.config import settings
from app.models import Incident, MaintenanceWindow, Probe, ProbeEvent, ProbeResult
from app.models.monitoring import (
    STATUS_DEGRADED,
    STATUS_DOWN,
    STATUS_MAINTENANCE,
    STATUS_UNKNOWN,
    STATUS_UP,
)
from app.probes import ProbeOutcome, run_probe
from app.realtime import publish_status_update
from app.status_service import find_page_for_probe
from app.uplink import uplink_ok

logger = logging.getLogger("probe_runner")


def _is_bad(status: str) -> bool:
    return status != STATUS_UP and status != STATUS_UNKNOWN


def _display_status(probe: Probe, raw_status: str, consecutive_failures: int) -> str:
    """Recorded/displayed status for a check, derived from the raw outcome.

    Two layers, both keyed off the consecutive-failure count:

    1. **Tolerance (monitoring noise):** the first ``tolerance_checks`` bad checks
       in a row — of *any* severity (degraded or down) — are treated as noise and
       recorded as ``up``, so isolated blips (a latency spike, one packet-loss
       sample, a single failed request) don't affect uptime, graphs or the
       timeline. A real outage lasting longer than the tolerance counts fully.
    2. **Down tiering:** past the tolerance, a hard-``down`` outcome shows as
       ``degraded`` until it reaches ``down_threshold`` (then ``down``).

    ``up``/``unknown`` pass through. Incident/alert logic uses the raw status, so
    this only changes what's stored/shown, not when alerts fire.
    """
    if raw_status in (STATUS_UP, STATUS_UNKNOWN):
        return raw_status
    # Layer 1: within tolerance -> treat as monitoring noise.
    if consecutive_failures <= probe.tolerance_checks:
        return STATUS_UP
    # Layer 2: down tiering, counted from the first non-tolerated failure
    # (degraded outcomes pass through unchanged).
    eff = consecutive_failures - probe.tolerance_checks
    if raw_status != STATUS_DOWN:
        return raw_status
    if eff >= max(1, probe.down_threshold):
        return STATUS_DOWN
    if eff >= max(1, probe.degraded_threshold):
        return STATUS_DEGRADED
    return STATUS_UP


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
            meta=outcome.meta,
        )
    return outcome


def _soften_timeout(outcome: ProbeOutcome) -> ProbeOutcome:
    """A timeout is "no response in time" — slow, not necessarily a hard outage,
    so render it as ``degraded`` instead of ``down``. It still counts as bad, so a
    *persistent* timeout opens an incident as usual — just not coloured red. Hard
    failures (connection refused, DNS, TLS, unexpected status) stay ``down``."""
    if outcome.kind == "timeout" and outcome.status == STATUS_DOWN:
        return ProbeOutcome(
            status=STATUS_DEGRADED, latency_ms=outcome.latency_ms,
            error=outcome.error, kind="timeout", meta=outcome.meta,
        )
    return outcome


def _run_check(probe: Probe, host: str) -> ProbeOutcome:
    """Run the probe with in-check retries. A bad attempt is retried up to
    ``probe.retries`` times (with a short backoff) before the result is accepted,
    so a single transient blip never registers as a failure. The first good
    attempt wins immediately; otherwise the last attempt's outcome is returned."""
    attempts = max(0, probe.retries) + 1
    outcome: ProbeOutcome | None = None
    for i in range(attempts):
        if i:
            time.sleep(max(0.0, settings.probe_retry_backoff_sec))
        raw = run_probe(probe.type, host, probe.config or {}, probe.timeout_sec)
        outcome = _apply_latency_threshold(probe, _soften_timeout(raw))
        if not _is_bad(outcome.status):
            break
    return outcome  # type: ignore[return-value]  # loop runs at least once


def _store_suppressed(db: Session, probe: Probe, now: datetime, outcome: ProbeOutcome) -> ProbeOutcome:
    """Record an inconclusive check when the worker's own uplink is down: write an
    ``unknown`` tick (excluded from uptime), refresh live status to ``unknown``,
    but leave the failure/success streaks and any open incident untouched and send
    no alerts. Returns the real probe outcome to the caller unchanged."""
    page = find_page_for_probe(db, probe.id)
    err = "monitor uplink down — check suppressed"
    db.add(
        ProbeResult(
            probe_id=probe.id, status=STATUS_UNKNOWN, latency_ms=None,
            error=err, checked_at=now,
        )
    )
    probe.last_status = STATUS_UNKNOWN
    probe.last_latency_ms = None
    probe.last_checked_at = now
    probe.last_error = err
    db.commit()
    if page is not None:
        publish_status_update(page.slug, {"probe_id": probe.id, "status": STATUS_UNKNOWN})
    logger.warning(
        "probe %s (%s %s): uplink down -> suppressed bad result (%s)",
        probe.id, probe.type, probe.server.host, outcome.error,
    )
    return outcome


def _apply_cert_meta(probe: Probe, meta: dict) -> None:
    """Denormalise TLS certificate metadata from a probe outcome onto the probe,
    so dashboards can show expiry/issuer without re-fetching the cert."""
    def _dt(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    probe.tls_expires_at = _dt(meta.get("expires_at"))
    probe.tls_not_before = _dt(meta.get("not_before"))
    probe.tls_issuer = meta.get("issuer")
    probe.tls_subject = meta.get("subject")
    sans = meta.get("sans") or []
    probe.tls_sans = ", ".join(sans) if sans else None
    probe.tls_fingerprint = meta.get("fingerprint")


def _evaluate_heartbeat(probe: Probe, now: datetime) -> ProbeOutcome:
    """A heartbeat probe is up while pings arrive within interval + grace."""
    grace = int((probe.config or {}).get("grace_sec", 0))
    if probe.last_ping_at is None:
        return ProbeOutcome(status=STATUS_UNKNOWN, error="no ping received yet")
    age = (now - probe.last_ping_at).total_seconds()
    if age <= probe.interval_sec + grace:
        return ProbeOutcome(status=STATUS_UP, latency_ms=None)
    return ProbeOutcome(status=STATUS_DOWN, error=f"no ping for {int(age)}s")


def _record_event(db: Session, probe: Probe, type_: str, detail: str, now: datetime) -> None:
    """Persist a change event for the admin activity feed (best-effort log kept
    independently of whether any alert channel is configured)."""
    db.add(ProbeEvent(probe_id=probe.id, type=type_, detail=detail, created_at=now))


def _alert_common(probe: Probe, host: str, outcome: ProbeOutcome, now: datetime) -> dict:
    """Static context shared by all alert kinds (server/service/page names + link)."""
    server = probe.server
    service = getattr(server, "service", None)
    page = getattr(service, "page", None) if service is not None else None
    page_url = (
        f"{settings.public_base_url.rstrip('/')}/status/{page.slug}"
        if page is not None and settings.public_base_url
        else ""
    )
    return dict(
        probe_name=probe.name, server_host=host, status=outcome.status, error=outcome.error,
        server_id=probe.server_id, probe_type=probe.type, server_name=server.name,
        service_name=service.name if service is not None else "",
        page_title=page.title if page is not None else "", page_url=page_url,
        latency_ms=outcome.latency_ms, occurred_at=now,
    )


def handle_ip_change(db: Session, probe: Probe, outcome: ProbeOutcome, host: str,
                     page_id: int | None, maint: bool, now: datetime) -> None:
    """For HTTP probes with IP tracking on: compare the freshly resolved address
    set with the last observed one, persist it, and fire a one-off ``ip_changed``
    alert when it changes. This is informational — it never opens an incident or
    affects uptime. The first observation is recorded silently (no baseline yet)."""
    ips = (outcome.meta or {}).get("resolved_ips")
    if not ips:
        return
    new_ip = ", ".join(ips)
    old_ip = probe.last_ip
    probe.last_ip = new_ip
    if old_ip and old_ip != new_ip and not maint:
        detail = f"{old_ip} → {new_ip}"
        _record_event(db, probe, "ip_changed", detail, now)
        common = _alert_common(probe, host, outcome, now)
        common["error"] = detail
        # group=False: each IP change is a distinct, meaningful event and must not
        # be coalesced with the server's down/resolved storm-grouping window.
        dispatch(base_channels(db, page_id), event="ip_changed", group=False, **common)


def handle_content_change(db: Session, probe: Probe, outcome: ProbeOutcome, host: str,
                          page_id: int | None, maint: bool, now: datetime) -> None:
    """For HTTP probes with content tracking on (config.track_content): compare the
    response-body hash with the last one, persist it, and fire a one-off
    ``content_changed`` alert when it changes. Informational — never opens an
    incident or affects uptime. The first observation is recorded silently."""
    new_hash = (outcome.meta or {}).get("content_hash")
    if not new_hash:
        return
    old_hash = probe.last_content_hash
    probe.last_content_hash = new_hash
    if old_hash and old_hash != new_hash and not maint:
        detail = f"{old_hash[:12]}… → {new_hash[:12]}…"
        _record_event(db, probe, "content_changed", detail, now)
        common = _alert_common(probe, host, outcome, now)
        common["error"] = detail
        dispatch(base_channels(db, page_id), event="content_changed", group=False, **common)


# Default cert-expiry reminder lead times (days). A reminder fires once as the
# certificate crosses into each ever-tighter bucket. Overridable per probe via
# config["cert_reminder_days"].
DEFAULT_CERT_REMINDER_DAYS = (14, 7, 1)


def _cert_reminder_bucket(days_left: int, thresholds) -> int | None:
    """The tightest reminder threshold the certificate has entered, or None when it
    still has more days left than the widest threshold."""
    hit = [t for t in thresholds if days_left <= t]
    return min(hit) if hit else None


def handle_cert_expiry(db: Session, probe: Probe, outcome: ProbeOutcome, host: str,
                       page_id: int | None, maint: bool, now: datetime) -> None:
    """For probes with cert-expiry reminders on (config.cert_expiry_reminders): send
    a proactive ``cert_expiring`` alert as the certificate crosses each lead-time
    threshold (default 14/7/1 days), once per threshold. Resets when the cert renews
    (plenty of days again), so the next cycle reminds afresh. Suppressed during
    maintenance without consuming the reminder, so it still fires afterwards."""
    if not (probe.config or {}).get("cert_expiry_reminders") or probe.tls_expires_at is None:
        return
    if maint:
        return
    days_left = (probe.tls_expires_at - now).days
    raw = (probe.config or {}).get("cert_reminder_days") or DEFAULT_CERT_REMINDER_DAYS
    thresholds = sorted({int(t) for t in raw if int(t) > 0})
    bucket = _cert_reminder_bucket(days_left, thresholds)
    if bucket is None:
        probe.tls_reminder_days = None  # healthy/renewed -> clear so it re-arms
        return
    if probe.tls_reminder_days == bucket:
        return  # already reminded at this urgency
    probe.tls_reminder_days = bucket

    when = probe.tls_expires_at.strftime("%d.%m.%Y")
    detail = (f"истёк {-days_left} дн. назад · {when}" if days_left < 0
              else f"истекает через {days_left} дн. · {when}")
    if probe.tls_issuer:
        detail += f" · издатель: {probe.tls_issuer}"
    _record_event(db, probe, "cert_expiring", detail, now)
    common = _alert_common(probe, host, outcome, now)
    common["error"] = detail
    dispatch(base_channels(db, page_id), event="cert_expiring", group=False, **common)


def handle_cert_change(db: Session, probe: Probe, outcome: ProbeOutcome, host: str,
                       page_id: int | None, maint: bool, now: datetime,
                       prev: tuple[str | None, str | None, str | None]) -> None:
    """For probes with SSL-change tracking on (config.track_cert_change): fire a
    one-off ``cert_changed`` alert when the served certificate's fingerprint
    differs from the previous check. Informational — never opens an incident or
    affects uptime. ``prev`` is the (fingerprint, issuer, subject) snapshot taken
    *before* this check's metadata was applied. The first observation and checks
    that couldn't read a fingerprint are silent."""
    if not (probe.config or {}).get("track_cert_change"):
        return
    new_fp = (outcome.meta or {}).get("fingerprint")
    if not new_fp:
        return
    prev_fp, prev_issuer, prev_subject = prev
    if not prev_fp or prev_fp == new_fp or maint:
        return

    # Build a human summary: highlight issuer/subject moves (the meaningful,
    # security-relevant changes), else note it's a renewal with the new expiry.
    changes: list[str] = []
    if prev_issuer != probe.tls_issuer:
        changes.append(f"издатель: {prev_issuer or '—'} → {probe.tls_issuer or '—'}")
    if prev_subject != probe.tls_subject:
        changes.append(f"subject: {prev_subject or '—'} → {probe.tls_subject or '—'}")
    if not changes:
        changes.append(f"издатель: {probe.tls_issuer or '—'}")
    if probe.tls_expires_at is not None:
        changes.append(f"действует до {probe.tls_expires_at.strftime('%d.%m.%Y')}")

    detail = " · ".join(changes)
    _record_event(db, probe, "cert_changed", detail, now)
    common = _alert_common(probe, host, outcome, now)
    common["error"] = detail
    dispatch(base_channels(db, page_id), event="cert_changed", group=False, **common)


def handle_incident(db: Session, probe: Probe, outcome: ProbeOutcome, host: str,
                    page_id: int | None, maint: bool, now: datetime) -> None:
    open_incident = (
        db.query(Incident)
        .filter(Incident.probe_id == probe.id, Incident.resolved_at.is_(None))
        .first()
    )
    bad = _is_bad(outcome.status)
    # An incident opens only once failures exceed both the flap guard and the
    # monitoring-noise tolerance — so tolerated blips never become a "сбой"
    # (consistent with them being excluded from uptime/graphs/timeline).
    threshold = max(1, probe.failure_threshold, probe.tolerance_checks + 1)

    common = _alert_common(probe, host, outcome, now)

    if bad:
        if open_incident is None:
            if probe.consecutive_failures >= threshold and not maint:
                db.add(Incident(probe_id=probe.id, last_status=outcome.status,
                                 last_notified_at=now, alert_count=1))
                window = get_alert_storm_window_sec(db)
                service_id = probe.server.service_id if probe.server is not None else None
                if window > 0 and service_id is not None:
                    try:
                        queue_storm_alert(service_id=service_id, page_id=page_id,
                                           window_sec=window, record={**common, "started_at": now})
                    except Exception as exc:  # noqa: BLE001 — Redis down: don't lose the alert
                        logger.warning("storm queue failed, sending immediately: %s", exc)
                        dispatch(base_channels(db, page_id), event="opened", started_at=now, **common)
                else:
                    dispatch(base_channels(db, page_id), event="opened", started_at=now, **common)
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
                    dispatch(base_channels(db, page_id), event="ongoing",
                             started_at=open_incident.started_at, **common)
                    open_incident.last_notified_at = now
                    open_incident.alert_count += 1
            # escalate once to escalation channels whose threshold has elapsed
            if open_incident.escalated_at is None:
                esc = escalation_channels(db, page_id, int(age_min))
                if esc:
                    dispatch(esc, event="escalated", started_at=open_incident.started_at, **common)
                    open_incident.escalated_at = now
                    open_incident.alert_count += 1
    elif open_incident is not None and probe.consecutive_successes >= max(1, probe.recovery_threshold):
        # Recovery confirmed (enough good checks in a row) -> close the incident.
        started = open_incident.started_at
        alert_count = open_incident.alert_count + 1  # + this resolved alert itself
        open_incident.resolved_at = now
        if not maint:
            dispatch(base_channels(db, page_id), event="resolved", started_at=started,
                     alert_count=alert_count, **common)
            if open_incident.escalated_at is not None:
                dispatch(all_escalation_channels(db, page_id), event="resolved",
                         group=False, started_at=started, alert_count=alert_count, **common)


def execute_and_store(db: Session, probe: Probe) -> ProbeOutcome:
    """Run one probe check, persist result + denormalised status + incident, publish."""
    host = probe.server.host
    now = datetime.now(timezone.utc)

    if probe.type == "heartbeat":
        outcome = _evaluate_heartbeat(probe, now)
    else:
        # In-check retries + timeout-softening + latency tiering all happen here.
        outcome = _run_check(probe, host)

    # Anti-storm gate: a bad result while the worker's *own* uplink is down almost
    # always means the monitor is offline, not the target. Record an inconclusive
    # tick and skip incident/alert logic instead of flooding the dashboard with
    # false "down"s. (Only consulted when the result is already bad, so healthy
    # checks pay nothing.)
    if _is_bad(outcome.status) and not uplink_ok():
        return _store_suppressed(db, probe, now, outcome)

    # Track consecutive failures (for tiering) and successes (for recovery
    # confirmation) from the RAW outcome, then tier it for display.
    if _is_bad(outcome.status):
        probe.consecutive_failures += 1
        probe.consecutive_successes = 0
    else:
        probe.consecutive_failures = 0
        probe.consecutive_successes += 1
    display = _display_status(probe, outcome.status, probe.consecutive_failures)

    page = find_page_for_probe(db, probe.id)
    maint = page is not None and in_maintenance(db, page.id, now)
    # Checks during a maintenance window are recorded as "maintenance" so they're
    # excluded from uptime/graphs/rollups (planned work shouldn't count as downtime).
    stored = STATUS_MAINTENANCE if maint else display

    db.add(
        ProbeResult(
            probe_id=probe.id, status=stored, latency_ms=outcome.latency_ms,
            error=outcome.error, checked_at=now,
        )
    )
    probe.last_status = display  # live status still reflects reality during maintenance
    probe.last_latency_ms = outcome.latency_ms
    probe.last_checked_at = now
    probe.last_error = outcome.error
    # Snapshot the stored certificate identity before this check overwrites it, so
    # SSL-change detection can compare against the previous certificate.
    prev_cert = (probe.tls_fingerprint, probe.tls_issuer, probe.tls_subject)
    if outcome.meta and ("expires_at" in outcome.meta or "fingerprint" in outcome.meta):
        _apply_cert_meta(probe, outcome.meta)
    db.flush()

    page_id = page.id if page else None
    # Incident/alert logic intentionally keys off the raw outcome + thresholds,
    # independently of the visual degraded/down tiering above.
    handle_incident(db, probe, outcome, host, page_id, maint, now)
    # Change/expiry tracking is independent of incidents (informational, no
    # downtime): resolved-IP set, response-body content, certificate identity and
    # certificate expiry lead times.
    handle_ip_change(db, probe, outcome, host, page_id, maint, now)
    handle_content_change(db, probe, outcome, host, page_id, maint, now)
    handle_cert_change(db, probe, outcome, host, page_id, maint, now, prev_cert)
    handle_cert_expiry(db, probe, outcome, host, page_id, maint, now)
    db.commit()

    if page is not None:
        publish_status_update(page.slug, {"probe_id": probe.id, "status": display})

    logger.info(
        "probe %s (%s %s) -> %s%s%s",
        probe.id, probe.type, host, outcome.status,
        f" ({outcome.error})" if outcome.error else "",
        " [maintenance]" if maint else "",
    )
    return outcome
