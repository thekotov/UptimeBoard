"""Public heartbeat ingest endpoint.

External jobs (cron, backups, …) call GET/POST /api/ping/<token> to report they
are alive. A heartbeat probe is marked up on each ping and goes down when pings
stop arriving within its interval (evaluated by the worker).
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.alerts import all_escalation_channels, base_channels, dispatch
from app.db import get_db
from app.models import Incident, Probe, ProbeResult
from app.models.monitoring import STATUS_UP
from app.realtime import publish_status_update
from app.status_service import find_page_for_probe

router = APIRouter(prefix="/api/ping", tags=["ping"])


@router.api_route("/{token}", methods=["GET", "POST"])
def ping(token: str, db: Session = Depends(get_db)):
    probe = (
        db.query(Probe)
        .filter(Probe.type == "heartbeat", Probe.config["token"].astext == token)
        .first()
    )
    if probe is None:
        raise HTTPException(status_code=404, detail="Unknown heartbeat token")

    now = datetime.now(timezone.utc)
    probe.last_ping_at = now
    probe.last_status = STATUS_UP
    probe.last_checked_at = now
    probe.last_error = None
    probe.consecutive_failures = 0
    db.add(ProbeResult(probe_id=probe.id, status=STATUS_UP, latency_ms=None, error=None, checked_at=now))
    db.flush()

    page = find_page_for_probe(db, probe.id)
    open_incident = (
        db.query(Incident)
        .filter(Incident.probe_id == probe.id, Incident.resolved_at.is_(None))
        .first()
    )
    if open_incident is not None:
        open_incident.resolved_at = now
        page_id = page.id if page else None
        common = dict(probe_name=probe.name, server_host=probe.server.host,
                      status=STATUS_UP, error=None, server_id=probe.server_id)
        dispatch(base_channels(db, page_id), event="resolved", **common)
        if open_incident.escalated_at is not None:
            dispatch(all_escalation_channels(db, page_id), event="resolved", group=False, **common)

    db.commit()
    if page is not None:
        publish_status_update(page.slug, {"probe_id": probe.id, "status": STATUS_UP})
    return {"ok": True}
