from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from datetime import datetime, timezone
from uuid import uuid4

from app.auth.deps import get_current_user
from app.db import get_db
from app.models import (
    AlertChannel,
    Announcement,
    Incident,
    MaintenanceWindow,
    Page,
    Probe,
    ProbeResult,
    Server,
    Service,
    User,
)
from app.schemas.admin import (
    AlertChannelCreate,
    AlertChannelOut,
    AlertChannelTest,
    AlertChannelUpdate,
    AnnouncementCreate,
    AnnouncementOut,
    MaintenanceCreate,
    MaintenanceOut,
    PageCreate,
    PageDetailOut,
    PageOut,
    PageUpdate,
    ProbeCreate,
    ProbeOut,
    ProbeTest,
    ProbeUpdate,
    ServerCreate,
    ServerOut,
    ServerUpdate,
    ServiceCreate,
    ServiceOut,
    ServiceUpdate,
)
from app.realtime import get_sync_redis
from app.config import settings
from app.alerts import test_telegram
from app.probe_runner import execute_and_store
from app.probes import run_probe
from app.schemas.public import IncidentItem

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(get_current_user)],
)


def _get_or_404(db: Session, model, obj_id: int):
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{model.__name__} not found")
    return obj


def _notify_schedule_changed() -> None:
    """Tell the worker to reload its probe schedule."""
    try:
        get_sync_redis().publish(settings.redis_status_channel + ":reload", "1")
    except Exception:  # noqa: BLE001
        pass


# ---------------- Pages ----------------


@router.get("/pages", response_model=list[PageOut])
def list_pages(db: Session = Depends(get_db)):
    return db.query(Page).order_by(Page.id).all()


@router.get("/slug-available")
def slug_available(slug: str, exclude_id: int | None = None, db: Session = Depends(get_db)):
    """Live check used by the admin UI: is this page slug free?"""
    q = db.query(Page.id).filter(Page.slug == slug)
    if exclude_id is not None:
        q = q.filter(Page.id != exclude_id)
    return {"available": q.first() is None}


@router.post("/pages", response_model=PageOut, status_code=201)
def create_page(payload: PageCreate, db: Session = Depends(get_db)):
    page = Page(**payload.model_dump())
    db.add(page)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="slug already exists")
    db.refresh(page)
    return page


@router.get("/pages/{page_id}", response_model=PageDetailOut)
def get_page(page_id: int, db: Session = Depends(get_db)):
    page = (
        db.query(Page)
        .options(
            selectinload(Page.services)
            .selectinload(Service.servers)
            .selectinload(Server.probes)
        )
        .filter(Page.id == page_id)
        .first()
    )
    if page is None:
        raise HTTPException(status_code=404, detail="Page not found")
    return page


@router.patch("/pages/{page_id}", response_model=PageOut)
def update_page(page_id: int, payload: PageUpdate, db: Session = Depends(get_db)):
    page = _get_or_404(db, Page, page_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(page, k, v)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="slug already exists")
    db.refresh(page)
    return page


@router.delete("/pages/{page_id}", status_code=204)
def delete_page(page_id: int, db: Session = Depends(get_db)):
    db.delete(_get_or_404(db, Page, page_id))
    db.commit()
    _notify_schedule_changed()


# ---------------- Services ----------------


@router.post("/services", response_model=ServiceOut, status_code=201)
def create_service(payload: ServiceCreate, db: Session = Depends(get_db)):
    _get_or_404(db, Page, payload.page_id)
    service = Service(**payload.model_dump())
    db.add(service)
    db.commit()
    db.refresh(service)
    return service


@router.patch("/services/{service_id}", response_model=ServiceOut)
def update_service(service_id: int, payload: ServiceUpdate, db: Session = Depends(get_db)):
    service = _get_or_404(db, Service, service_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(service, k, v)
    db.commit()
    db.refresh(service)
    return service


@router.delete("/services/{service_id}", status_code=204)
def delete_service(service_id: int, db: Session = Depends(get_db)):
    db.delete(_get_or_404(db, Service, service_id))
    db.commit()
    _notify_schedule_changed()


# ---------------- Servers ----------------


@router.post("/servers", response_model=ServerOut, status_code=201)
def create_server(payload: ServerCreate, db: Session = Depends(get_db)):
    _get_or_404(db, Service, payload.service_id)
    server = Server(**payload.model_dump())
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


@router.patch("/servers/{server_id}", response_model=ServerOut)
def update_server(server_id: int, payload: ServerUpdate, db: Session = Depends(get_db)):
    server = _get_or_404(db, Server, server_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(server, k, v)
    db.commit()
    db.refresh(server)
    return server


@router.delete("/servers/{server_id}", status_code=204)
def delete_server(server_id: int, db: Session = Depends(get_db)):
    db.delete(_get_or_404(db, Server, server_id))
    db.commit()
    _notify_schedule_changed()


# ---------------- Probes ----------------


@router.post("/probes", response_model=ProbeOut, status_code=201)
def create_probe(payload: ProbeCreate, db: Session = Depends(get_db)):
    _get_or_404(db, Server, payload.server_id)
    data = payload.model_dump()
    # Heartbeat probes get a unique push token used in their /api/ping/<token> URL.
    if data["type"] == "heartbeat" and not (data.get("config") or {}).get("token"):
        data.setdefault("config", {})
        data["config"]["token"] = uuid4().hex
    probe = Probe(**data)
    db.add(probe)
    db.commit()
    db.refresh(probe)
    _notify_schedule_changed()
    return probe


@router.patch("/probes/{probe_id}", response_model=ProbeOut)
def update_probe(probe_id: int, payload: ProbeUpdate, db: Session = Depends(get_db)):
    probe = _get_or_404(db, Probe, probe_id)
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(probe, k, v)
    # Pausing a probe: close any open incident (silently) and clear the failure
    # counter so it doesn't immediately re-alert when resumed.
    if data.get("enabled") is False:
        probe.consecutive_failures = 0
        (
            db.query(Incident)
            .filter(Incident.probe_id == probe.id, Incident.resolved_at.is_(None))
            .update({Incident.resolved_at: datetime.now(timezone.utc)}, synchronize_session=False)
        )
    db.commit()
    db.refresh(probe)
    _notify_schedule_changed()
    return probe


@router.delete("/probes/{probe_id}", status_code=204)
def delete_probe(probe_id: int, db: Session = Depends(get_db)):
    db.delete(_get_or_404(db, Probe, probe_id))
    db.commit()
    _notify_schedule_changed()


@router.post("/probes/{probe_id}/check")
def check_probe_now(probe_id: int, db: Session = Depends(get_db)):
    """Run the probe immediately and return the outcome (on-demand check)."""
    probe = _get_or_404(db, Probe, probe_id)
    outcome = execute_and_store(db, probe)
    return {
        "status": outcome.status,
        "latency_ms": outcome.latency_ms,
        "error": outcome.error,
    }


@router.post("/probes/test")
def test_probe(payload: ProbeTest, db: Session = Depends(get_db)):
    """Run a probe configuration WITHOUT saving it (test-before-save)."""
    host = payload.host
    if not host and payload.server_id:
        server = _get_or_404(db, Server, payload.server_id)
        host = server.host
    if not host:
        raise HTTPException(status_code=400, detail="host or server_id required")
    outcome = run_probe(payload.type, host, payload.config or {}, payload.timeout_sec)
    return {"status": outcome.status, "latency_ms": outcome.latency_ms, "error": outcome.error}


def _clone_probe(src: Probe, *, copy_name: bool = True) -> Probe:
    """Build a detached copy of a probe (FK is set by the owning relationship)."""
    return Probe(
        name=f"{src.name} (copy)" if copy_name else src.name, type=src.type,
        interval_sec=src.interval_sec, timeout_sec=src.timeout_sec, enabled=src.enabled,
        failure_threshold=src.failure_threshold, latency_degraded_ms=src.latency_degraded_ms,
        degraded_threshold=src.degraded_threshold, down_threshold=src.down_threshold,
        config=dict(src.config or {}),
    )


@router.post("/probes/{probe_id}/clone", response_model=ProbeOut, status_code=201)
def clone_probe(probe_id: int, db: Session = Depends(get_db)):
    src = _get_or_404(db, Probe, probe_id)
    clone = _clone_probe(src)
    clone.server_id = src.server_id
    db.add(clone)
    db.commit()
    db.refresh(clone)
    _notify_schedule_changed()
    return clone


@router.post("/servers/{server_id}/clone", response_model=ServerOut, status_code=201)
def clone_server(server_id: int, db: Session = Depends(get_db)):
    src = _get_or_404(db, Server, server_id)
    clone = Server(service_id=src.service_id, name=f"{src.name} (copy)", host=src.host, order=src.order)
    clone.probes = [_clone_probe(p) for p in src.probes]
    db.add(clone)
    db.commit()
    db.refresh(clone)
    _notify_schedule_changed()
    return clone


@router.post("/services/{service_id}/clone", response_model=ServiceOut, status_code=201)
def clone_service(service_id: int, db: Session = Depends(get_db)):
    src = (
        db.query(Service)
        .options(selectinload(Service.servers).selectinload(Server.probes))
        .filter(Service.id == service_id)
        .first()
    )
    if src is None:
        raise HTTPException(status_code=404, detail="Service not found")
    clone = Service(page_id=src.page_id, name=f"{src.name} (copy)", order=src.order)
    clone.servers = [
        Server(name=s.name, host=s.host, order=s.order, probes=[_clone_probe(p) for p in s.probes])
        for s in src.servers
    ]
    db.add(clone)
    db.commit()
    db.refresh(clone)
    _notify_schedule_changed()
    return clone


@router.post("/pages/{page_id}/clone", response_model=PageOut, status_code=201)
def clone_page(page_id: int, db: Session = Depends(get_db)):
    src = (
        db.query(Page)
        .options(selectinload(Page.services).selectinload(Service.servers).selectinload(Server.probes))
        .filter(Page.id == page_id)
        .first()
    )
    if src is None:
        raise HTTPException(status_code=404, detail="Page not found")

    # Find an available slug: <slug>-copy, -copy-2, ...
    base = f"{src.slug}-copy"
    slug = base
    n = 1
    while db.query(Page.id).filter(Page.slug == slug).first() is not None:
        n += 1
        slug = f"{base}-{n}"

    clone = Page(
        slug=slug, title=f"{src.title} (copy)", description=src.description,
        group_name=src.group_name, is_published=False,
    )
    clone.services = [
        Service(
            name=svc.name, order=svc.order,
            servers=[
                Server(name=s.name, host=s.host, order=s.order,
                       probes=[_clone_probe(p) for p in s.probes])
                for s in svc.servers
            ],
        )
        for svc in src.services
    ]
    db.add(clone)
    db.commit()
    db.refresh(clone)
    _notify_schedule_changed()
    return clone


# ---------------- Config export / import ----------------


def _unique_slug(db: Session, base: str) -> str:
    slug, n = base, 1
    while db.query(Page.id).filter(Page.slug == slug).first() is not None:
        n += 1
        slug = f"{base}-{n}"
    return slug


@router.get("/pages/{page_id}/export")
def export_page(page_id: int, db: Session = Depends(get_db)):
    """Export a page (with its services/servers/probes) as portable JSON."""
    page = (
        db.query(Page)
        .options(selectinload(Page.services).selectinload(Service.servers).selectinload(Server.probes))
        .filter(Page.id == page_id)
        .first()
    )
    if page is None:
        raise HTTPException(status_code=404, detail="Page not found")
    return {
        "version": 1,
        "slug": page.slug,
        "title": page.title,
        "description": page.description,
        "group_name": page.group_name,
        "services": [
            {
                "name": svc.name, "order": svc.order,
                "servers": [
                    {
                        "name": s.name, "host": s.host, "order": s.order,
                        "probes": [
                            {
                                "name": p.name, "type": p.type, "interval_sec": p.interval_sec,
                                "timeout_sec": p.timeout_sec, "enabled": p.enabled, "order": p.order,
                                "failure_threshold": p.failure_threshold,
                                "latency_degraded_ms": p.latency_degraded_ms,
                                "degraded_threshold": p.degraded_threshold,
                                "down_threshold": p.down_threshold, "config": p.config,
                            }
                            for p in s.probes
                        ],
                    }
                    for s in svc.servers
                ],
            }
            for svc in page.services
        ],
    }


@router.post("/pages/import", response_model=PageOut, status_code=201)
def import_page(payload: dict = Body(...), db: Session = Depends(get_db)):
    """Create a new (unpublished) page from exported JSON. Slug is made unique."""
    if not isinstance(payload, dict) or "title" not in payload:
        raise HTTPException(status_code=400, detail="invalid import data")

    base_slug = (payload.get("slug") or "imported").strip() or "imported"
    page = Page(
        slug=_unique_slug(db, base_slug),
        title=payload.get("title", "Imported"),
        description=payload.get("description"),
        group_name=payload.get("group_name"),
        is_published=False,
    )
    page.services = [
        Service(
            name=svc.get("name", "Service"), order=svc.get("order", 0),
            servers=[
                Server(
                    name=s.get("name", "server"), host=s.get("host", ""), order=s.get("order", 0),
                    probes=[
                        Probe(
                            name=p.get("name", "probe"), type=p.get("type", "http"),
                            interval_sec=p.get("interval_sec", 60), timeout_sec=p.get("timeout_sec", 10),
                            enabled=p.get("enabled", True), order=p.get("order", 0),
                            failure_threshold=p.get("failure_threshold", 1),
                            latency_degraded_ms=p.get("latency_degraded_ms"),
                            degraded_threshold=p.get("degraded_threshold", 1),
                            down_threshold=p.get("down_threshold", 1),
                            config=p.get("config", {}),
                        )
                        for p in s.get("probes", [])
                    ],
                )
                for s in svc.get("servers", [])
            ],
        )
        for svc in payload.get("services", [])
    ]
    db.add(page)
    db.commit()
    db.refresh(page)
    _notify_schedule_changed()
    return page


# ---------------- Maintenance windows ----------------


@router.get("/maintenance", response_model=list[MaintenanceOut])
def list_maintenance(page_id: int, db: Session = Depends(get_db)):
    return (
        db.query(MaintenanceWindow)
        .filter(MaintenanceWindow.page_id == page_id)
        .order_by(MaintenanceWindow.starts_at.desc())
        .all()
    )


@router.post("/maintenance", response_model=MaintenanceOut, status_code=201)
def create_maintenance(payload: MaintenanceCreate, db: Session = Depends(get_db)):
    _get_or_404(db, Page, payload.page_id)
    window = MaintenanceWindow(**payload.model_dump())
    db.add(window)
    db.commit()
    db.refresh(window)
    return window


@router.delete("/maintenance/{window_id}", status_code=204)
def delete_maintenance(window_id: int, db: Session = Depends(get_db)):
    db.delete(_get_or_404(db, MaintenanceWindow, window_id))
    db.commit()


# ---------------- Announcements ----------------


@router.get("/announcements", response_model=list[AnnouncementOut])
def list_announcements(page_id: int, db: Session = Depends(get_db)):
    return (
        db.query(Announcement)
        .filter(Announcement.page_id == page_id)
        .order_by(Announcement.starts_at.desc())
        .all()
    )


@router.post("/announcements", response_model=AnnouncementOut, status_code=201)
def create_announcement(payload: AnnouncementCreate, db: Session = Depends(get_db)):
    _get_or_404(db, Page, payload.page_id)
    ann = Announcement(**payload.model_dump())
    db.add(ann)
    db.commit()
    db.refresh(ann)
    return ann


@router.delete("/announcements/{ann_id}", status_code=204)
def delete_announcement(ann_id: int, db: Session = Depends(get_db)):
    db.delete(_get_or_404(db, Announcement, ann_id))
    db.commit()


# ---------------- Incidents (history management) ----------------


def _page_probe_ids(db: Session, page_id: int) -> list[int]:
    return [
        row[0]
        for row in (
            db.query(Probe.id)
            .join(Server, Probe.server_id == Server.id)
            .join(Service, Server.service_id == Service.id)
            .filter(Service.page_id == page_id)
            .all()
        )
    ]


def _incident_item(db: Session, inc: Incident) -> IncidentItem:
    probe, server, service = (
        db.query(Probe, Server, Service)
        .join(Server, Probe.server_id == Server.id)
        .join(Service, Server.service_id == Service.id)
        .filter(Probe.id == inc.probe_id)
        .one()
    )
    end = inc.resolved_at or datetime.now(timezone.utc)
    return IncidentItem(
        id=inc.id, probe_id=probe.id, probe_name=probe.name,
        server_name=server.name, service_name=service.name,
        last_status=inc.last_status, started_at=inc.started_at,
        resolved_at=inc.resolved_at, duration_sec=int((end - inc.started_at).total_seconds()),
        ongoing=inc.resolved_at is None, acknowledged_at=inc.acknowledged_at,
    )


@router.get("/pages/{page_id}/incidents", response_model=list[IncidentItem])
def list_page_incidents(page_id: int, limit: int = 100, db: Session = Depends(get_db)):
    _get_or_404(db, Page, page_id)
    rows = (
        db.query(Incident, Probe, Server, Service)
        .join(Probe, Incident.probe_id == Probe.id)
        .join(Server, Probe.server_id == Server.id)
        .join(Service, Server.service_id == Service.id)
        .filter(Service.page_id == page_id)
        .order_by(Incident.started_at.desc())
        .limit(limit)
        .all()
    )
    now = datetime.now(timezone.utc)
    items: list[IncidentItem] = []
    for inc, probe, server, service in rows:
        end = inc.resolved_at or now
        items.append(
            IncidentItem(
                id=inc.id, probe_id=probe.id, probe_name=probe.name,
                server_name=server.name, service_name=service.name,
                last_status=inc.last_status, started_at=inc.started_at,
                resolved_at=inc.resolved_at, duration_sec=int((end - inc.started_at).total_seconds()),
                ongoing=inc.resolved_at is None, acknowledged_at=inc.acknowledged_at,
            )
        )
    return items


@router.post("/incidents/{incident_id}/ack", response_model=IncidentItem)
def acknowledge_incident(incident_id: int, db: Session = Depends(get_db)):
    """Acknowledge an open incident: it stays open but repeat/escalation alerts
    are suppressed until it resolves or is un-acknowledged."""
    inc = _get_or_404(db, Incident, incident_id)
    if inc.resolved_at is None and inc.acknowledged_at is None:
        inc.acknowledged_at = datetime.now(timezone.utc)
        db.commit()
    return _incident_item(db, inc)


@router.post("/incidents/{incident_id}/unack", response_model=IncidentItem)
def unacknowledge_incident(incident_id: int, db: Session = Depends(get_db)):
    """Clear acknowledgement; repeat/escalation alerts resume on the next check."""
    inc = _get_or_404(db, Incident, incident_id)
    if inc.acknowledged_at is not None:
        inc.acknowledged_at = None
        db.commit()
    return _incident_item(db, inc)


@router.delete("/incidents/{incident_id}", status_code=204)
def delete_incident(incident_id: int, db: Session = Depends(get_db)):
    db.delete(_get_or_404(db, Incident, incident_id))
    db.commit()


@router.delete("/pages/{page_id}/incidents", status_code=204)
def clear_page_incidents(page_id: int, only_resolved: bool = False, db: Session = Depends(get_db)):
    """Delete incident history for a page (all, or only resolved ones)."""
    _get_or_404(db, Page, page_id)
    probe_ids = _page_probe_ids(db, page_id)
    if not probe_ids:
        return
    q = db.query(Incident).filter(Incident.probe_id.in_(probe_ids))
    if only_resolved:
        q = q.filter(Incident.resolved_at.is_not(None))
    q.delete(synchronize_session=False)
    db.commit()


# ---------------- Metrics ----------------


@router.delete("/metrics")
def clear_metrics(page_id: int | None = None, db: Session = Depends(get_db)):
    """Wipe stored probe results (timeline/uptime history).

    Without ``page_id`` clears every probe's history; with it, only that page's
    probes. Denormalised current statuses are left intact — they refresh on the
    next check. Returns how many result rows were removed.
    """
    q = db.query(ProbeResult)
    if page_id is not None:
        _get_or_404(db, Page, page_id)
        probe_ids = _page_probe_ids(db, page_id)
        if not probe_ids:
            return {"deleted": 0}
        q = q.filter(ProbeResult.probe_id.in_(probe_ids))
    deleted = q.delete(synchronize_session=False)
    db.commit()
    return {"deleted": deleted}


# ---------------- Alert channels ----------------

# Secret config keys per channel type — masked in responses, preserved when the
# client submits an empty value on update (so they aren't accidentally wiped).
_SECRET_KEYS = {"telegram": ["bot_token"], "webhook": ["secret_value"], "email": ["password"]}
_MASK = "••••••"


def _masked(channel: AlertChannel) -> AlertChannelOut:
    cfg = dict(channel.config or {})
    for key in _SECRET_KEYS.get(channel.type, []):
        if cfg.get(key):
            cfg[key] = _MASK
    return AlertChannelOut(
        id=channel.id, name=channel.name, type=channel.type, enabled=channel.enabled,
        page_id=channel.page_id, escalate_after_min=channel.escalate_after_min, config=cfg,
    )


@router.get("/alert-channels", response_model=list[AlertChannelOut])
def list_alert_channels(db: Session = Depends(get_db)):
    return [_masked(c) for c in db.query(AlertChannel).order_by(AlertChannel.id).all()]


@router.post("/alert-channels/test")
def test_alert_channel(payload: AlertChannelTest, db: Session = Depends(get_db)):
    """Test a channel's connectivity. Telegram: getMe via optional SOCKS5 proxy."""
    cfg = dict(payload.config or {})
    # If the secret/proxy weren't re-entered, fall back to the stored channel config.
    if payload.channel_id is not None:
        stored = db.get(AlertChannel, payload.channel_id)
        if stored is not None:
            for key in ("bot_token", "proxy"):
                if not cfg.get(key) or cfg.get(key) == _MASK:
                    cfg[key] = (stored.config or {}).get(key, cfg.get(key))
    if payload.type == "telegram":
        ok, detail = test_telegram(cfg.get("bot_token"), cfg.get("proxy"))
        return {"ok": ok, "detail": detail}
    return {"ok": False, "detail": "test not supported for this channel type"}


@router.post("/alert-channels", response_model=AlertChannelOut, status_code=201)
def create_alert_channel(payload: AlertChannelCreate, db: Session = Depends(get_db)):
    channel = AlertChannel(**payload.model_dump())
    db.add(channel)
    db.commit()
    db.refresh(channel)
    return _masked(channel)


@router.patch("/alert-channels/{channel_id}", response_model=AlertChannelOut)
def update_alert_channel(channel_id: int, payload: AlertChannelUpdate, db: Session = Depends(get_db)):
    channel = _get_or_404(db, AlertChannel, channel_id)
    data = payload.model_dump(exclude_unset=True)
    if "config" in data and data["config"] is not None:
        # Preserve stored secrets when the client leaves them blank or sends the mask.
        new_cfg = dict(data["config"])
        old_cfg = channel.config or {}
        for key in _SECRET_KEYS.get(channel.type, []):
            if not new_cfg.get(key) or new_cfg.get(key) == _MASK:
                if old_cfg.get(key):
                    new_cfg[key] = old_cfg[key]
                else:
                    new_cfg.pop(key, None)
        data["config"] = new_cfg
    for k, v in data.items():
        setattr(channel, k, v)
    db.commit()
    db.refresh(channel)
    return _masked(channel)


@router.delete("/alert-channels/{channel_id}", status_code=204)
def delete_alert_channel(channel_id: int, db: Session = Depends(get_db)):
    db.delete(_get_or_404(db, AlertChannel, channel_id))
    db.commit()
