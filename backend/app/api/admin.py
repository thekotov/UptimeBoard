import logging

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy import case, func, or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.auth.deps import get_current_user
from app.db import get_db
from app.models import (
    AlertChannel,
    Announcement,
    AppSettings,
    Incident,
    MaintenanceWindow,
    Page,
    Probe,
    ProbeEvent,
    ProbeResult,
    ProbeRollup,
    Server,
    Service,
    User,
)
from app.schemas.admin import (
    AlertChannelCreate,
    AlertChannelOut,
    AlertChannelMenuToggle,
    AlertChannelTest,
    AlertChannelUpdate,
    AlertSettingsOut,
    AlertSettingsUpdate,
    AnnouncementCreate,
    AnnouncementOut,
    MaintenanceCreate,
    MaintenanceOut,
    PageCreate,
    PageDetailOut,
    PageOut,
    PageUpdate,
    ProbeBase,
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
from app.realtime import get_sync_redis, get_worker_heartbeat_age
from app.config import settings
from app.alerts import (
    delete_telegram_webhook,
    get_alert_storm_window_sec,
    list_telegram_chats,
    record_deliveries,
    render_preview,
    send_test_email,
    send_test_telegram,
    send_test_webhook,
    set_telegram_webhook,
    test_email,
    test_telegram,
    test_webhook,
)
from app.probe_runner import execute_and_store
from app.probes import run_probe
from app.schemas.public import IncidentItem

logger = logging.getLogger("admin")

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
    except Exception as exc:  # noqa: BLE001
        # Best-effort: the worker still picks up schedule changes on its next
        # periodic reload, but log it — a silent failure here previously gave
        # no signal that the reload nudge was lost.
        logger.warning("schedule-reload notify failed: %s", exc)


# ---------------- Pages ----------------


@router.get("/pages", response_model=list[PageOut])
def list_pages(db: Session = Depends(get_db)):
    pages = db.query(Page).order_by(Page.id).all()
    out = [PageOut.model_validate(p) for p in pages]
    by_id = {p.id: o for p, o in zip(pages, out)}

    # Health: one pass over every probe's denormalised last-known state, bucketed
    # the same way the editor's liveStatus()/healthOf() do on the frontend — a
    # stale probe (no check within interval*3 + 30s) counts as neither ok/warn/bad.
    now = datetime.now(timezone.utc)
    probe_rows = (
        db.query(Service.page_id, Probe.enabled, Probe.last_status, Probe.last_checked_at, Probe.interval_sec)
        .join(Server, Probe.server_id == Server.id)
        .join(Service, Server.service_id == Service.id)
        .filter(Service.page_id.in_(by_id.keys()))
        .all()
    )
    for page_id, enabled, last_status, last_checked_at, interval_sec in probe_rows:
        if not enabled:
            continue
        if last_checked_at is None or (now - last_checked_at).total_seconds() > interval_sec * 3 + 30:
            continue
        if last_status in ("down",):
            by_id[page_id].health_bad += 1
        elif last_status in ("degraded",):
            by_id[page_id].health_warn += 1
        elif last_status in ("up", "recovered"):
            by_id[page_id].health_ok += 1

    # Uptime 24h: aggregate raw probe_results (no rollups exist yet for a 24h
    # window — see public.py get_timeline), same exclusion rule as everywhere
    # else: "maintenance" (planned) and "unknown" (no-confidence) aren't outages.
    since = now - timedelta(hours=24)
    uptime_rows = (
        db.query(
            Service.page_id,
            func.count(ProbeResult.id),
            func.sum(case((ProbeResult.status == "up", 1), else_=0)),
        )
        .select_from(ProbeResult)
        .join(Probe, ProbeResult.probe_id == Probe.id)
        .join(Server, Probe.server_id == Server.id)
        .join(Service, Server.service_id == Service.id)
        .filter(
            Service.page_id.in_(by_id.keys()),
            ProbeResult.checked_at >= since,
            ProbeResult.status.notin_(["maintenance", "unknown"]),
        )
        .group_by(Service.page_id)
        .all()
    )
    for page_id, total, up in uptime_rows:
        if total:
            by_id[page_id].uptime_pct_24h = round(up / total * 100, 2)

    return out


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
    now = datetime.now(timezone.utc)
    resp = PageDetailOut.model_validate(page)
    resp.active_announcements = (
        db.query(Announcement)
        .filter(Announcement.page_id == page_id, Announcement.starts_at <= now, Announcement.ends_at >= now)
        .count()
    )
    resp.active_maintenance = (
        db.query(MaintenanceWindow)
        .filter(MaintenanceWindow.page_id == page_id, MaintenanceWindow.starts_at <= now, MaintenanceWindow.ends_at >= now)
        .count()
    )
    return resp


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
    server = _get_or_404(db, Server, payload.server_id)
    data = payload.model_dump()
    # Inherit the page's default noise tolerance when the client didn't specify one.
    if data.get("tolerance_checks") is None:
        page = server.service.page if server.service is not None else None
        data["tolerance_checks"] = page.default_tolerance_checks if page is not None else 0
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


# Detection-tuning fields whose defaults the "reset to defaults" action restores.
# Kept narrow on purpose: type/URL/interval/timeout/name are left untouched.
PROBE_TUNING_FIELDS = (
    "failure_threshold",
    "degraded_threshold",
    "down_threshold",
    "tolerance_checks",
    "recovery_threshold",
    "retries",
)


@router.post("/probes/reset-defaults")
def reset_probe_defaults(db: Session = Depends(get_db)):
    """Reset the detection-tuning fields (thresholds, noise tolerance, retries) of
    every probe to the current defaults defined on ProbeBase. Leaves type, URL,
    interval, timeout, name and enabled state untouched."""
    defaults = {f: ProbeBase.model_fields[f].default for f in PROBE_TUNING_FIELDS}
    updated = db.query(Probe).update(defaults, synchronize_session=False)
    db.commit()
    _notify_schedule_changed()
    return {"updated": updated, "defaults": defaults}


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
        "meta": outcome.meta,
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
    return {"status": outcome.status, "latency_ms": outcome.latency_ms,
            "error": outcome.error, "meta": outcome.meta}


def _clone_probe(src: Probe, *, copy_name: bool = True) -> Probe:
    """Build a detached copy of a probe (FK is set by the owning relationship)."""
    return Probe(
        name=f"{src.name} (copy)" if copy_name else src.name, type=src.type,
        interval_sec=src.interval_sec, timeout_sec=src.timeout_sec, enabled=src.enabled,
        failure_threshold=src.failure_threshold, latency_degraded_ms=src.latency_degraded_ms,
        degraded_threshold=src.degraded_threshold, down_threshold=src.down_threshold,
        tolerance_checks=src.tolerance_checks, recovery_threshold=src.recovery_threshold,
        retries=src.retries, config=dict(src.config or {}),
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
    clone = Server(service_id=src.service_id, name=f"{src.name} (copy)", host=src.host, note=src.note, order=src.order)
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
        Server(name=s.name, host=s.host, note=s.note, order=s.order, probes=[_clone_probe(p) for p in s.probes])
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
        default_collapsed=src.default_collapsed,
        default_tolerance_checks=src.default_tolerance_checks,
        mask_ip=src.mask_ip,
    )
    clone.services = [
        Service(
            name=svc.name, order=svc.order,
            servers=[
                Server(name=s.name, host=s.host, note=s.note, order=s.order,
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
        "default_collapsed": page.default_collapsed,
        "default_tolerance_checks": page.default_tolerance_checks,
        "mask_ip": page.mask_ip,
        "services": [
            {
                "name": svc.name, "order": svc.order,
                "servers": [
                    {
                        "name": s.name, "host": s.host, "note": s.note, "order": s.order,
                        "probes": [
                            {
                                "name": p.name, "type": p.type, "interval_sec": p.interval_sec,
                                "timeout_sec": p.timeout_sec, "enabled": p.enabled, "order": p.order,
                                "failure_threshold": p.failure_threshold,
                                "latency_degraded_ms": p.latency_degraded_ms,
                                "degraded_threshold": p.degraded_threshold,
                                "down_threshold": p.down_threshold,
                                "tolerance_checks": p.tolerance_checks,
                                "recovery_threshold": p.recovery_threshold, "retries": p.retries,
                                "config": p.config,
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
        default_collapsed=payload.get("default_collapsed", True),
        default_tolerance_checks=payload.get("default_tolerance_checks", 0),
        mask_ip=payload.get("mask_ip", False),
        is_published=False,
    )
    page.services = [
        Service(
            name=svc.get("name", "Service"), order=svc.get("order", 0),
            servers=[
                Server(
                    name=s.get("name", "server"), host=s.get("host", ""),
                    note=s.get("note"), order=s.get("order", 0),
                    probes=[
                        Probe(
                            name=p.get("name", "probe"), type=p.get("type", "http"),
                            interval_sec=p.get("interval_sec", 60), timeout_sec=p.get("timeout_sec", 10),
                            enabled=p.get("enabled", True), order=p.get("order", 0),
                            failure_threshold=p.get("failure_threshold", 1),
                            latency_degraded_ms=p.get("latency_degraded_ms"),
                            degraded_threshold=p.get("degraded_threshold", 1),
                            down_threshold=p.get("down_threshold", 1),
                            tolerance_checks=p.get("tolerance_checks", 0),
                            recovery_threshold=p.get("recovery_threshold", 1),
                            retries=p.get("retries", 1),
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


# ---------------- Statistics ----------------


def _worker_status() -> dict:
    age = get_worker_heartbeat_age()
    if age is None:
        return {"status": "unknown", "last_beat_age_sec": None, "stale_after_sec": settings.worker_stale_sec}
    return {
        "status": "ok" if age <= settings.worker_stale_sec else "stale",
        "last_beat_age_sec": round(age, 1),
        "stale_after_sec": settings.worker_stale_sec,
    }


@router.get("/stats")
def admin_stats(db: Session = Depends(get_db)):
    """Database & metrics health: on-disk size per table (with estimated row
    counts from the planner stats — cheap even on huge tables), the metrics
    retention span and recent check volume, entity counts and worker liveness."""
    # Per-table size + estimated rows (n_live_tup avoids a full COUNT on big tables).
    tables = [
        {
            "name": row.relname,
            "rows": int(row.rows or 0),
            "total_bytes": int(row.total_bytes or 0),
            "table_bytes": int(row.table_bytes or 0),
            "index_bytes": int(row.index_bytes or 0),
        }
        for row in db.execute(text(
            """
            SELECT relname,
                   n_live_tup AS rows,
                   pg_total_relation_size(relid) AS total_bytes,
                   pg_table_size(relid)          AS table_bytes,
                   pg_indexes_size(relid)        AS index_bytes
            FROM pg_stat_user_tables
            ORDER BY pg_total_relation_size(relid) DESC
            """
        ))
    ]
    total_bytes = int(db.execute(text("SELECT pg_database_size(current_database())")).scalar() or 0)

    now = datetime.now(timezone.utc)

    # --- monitoring health: is the monitor itself doing its job? ---
    # Enabled probes that haven't been checked in ~3× their interval (or never).
    overdue: list[dict] = []
    for r in (
        db.query(
            Probe.id, Probe.name, Probe.type, Probe.interval_sec,
            Probe.last_checked_at, Server.name.label("server_name"),
            Page.slug.label("page_slug"), Page.title.label("page_title"),
        )
        .join(Server, Probe.server_id == Server.id)
        .join(Service, Server.service_id == Service.id)
        .join(Page, Service.page_id == Page.id)
        .filter(Probe.enabled.is_(True))
        .all()
    ):
        interval = r.interval_sec or 60
        age = (now - r.last_checked_at).total_seconds() if r.last_checked_at else None
        if age is None or age > interval * 3:
            overdue.append({
                "probe_id": r.id, "probe_name": r.name, "probe_type": r.type,
                "server_name": r.server_name, "page_slug": r.page_slug, "page_title": r.page_title,
                "interval_sec": interval,
                "last_checked_at": r.last_checked_at.isoformat() if r.last_checked_at else None,
                "overdue_sec": round(age) if age is not None else None,
            })
    # never-checked first, then longest overdue.
    overdue.sort(key=lambda x: (x["overdue_sec"] is None, x["overdue_sec"] or 0), reverse=True)

    failing_channels = [
        {
            "id": c.id, "name": c.name, "type": c.type, "last_error": c.last_error,
            "last_sent_at": c.last_sent_at.isoformat() if c.last_sent_at else None,
        }
        for c in db.query(AlertChannel).filter(AlertChannel.last_ok.is_(False)).all()
    ]

    oldest, newest = db.execute(
        text("SELECT min(checked_at), max(checked_at) FROM probe_results")
    ).one()
    results_24h = db.scalar(
        text("SELECT count(*) FROM probe_results WHERE checked_at >= :since"),
        {"since": now - timedelta(hours=24)},
    )
    results_1h = db.scalar(
        text("SELECT count(*) FROM probe_results WHERE checked_at >= :since"),
        {"since": now - timedelta(hours=1)},
    )

    return {
        "database": {"total_bytes": total_bytes, "tables": tables},
        "metrics": {
            "results_oldest": oldest.isoformat() if oldest else None,
            "results_newest": newest.isoformat() if newest else None,
            "results_last_24h": int(results_24h or 0),
            "results_last_1h": int(results_1h or 0),
            "rollups_hour": db.query(ProbeRollup).filter(ProbeRollup.period == "hour").count(),
            "rollups_day": db.query(ProbeRollup).filter(ProbeRollup.period == "day").count(),
        },
        "entities": {
            "pages": db.query(Page).count(),
            "services": db.query(Service).count(),
            "servers": db.query(Server).count(),
            "probes": db.query(Probe).count(),
            "probes_enabled": db.query(Probe).filter(Probe.enabled.is_(True)).count(),
            "incidents": db.query(Incident).count(),
            "incidents_open": db.query(Incident).filter(Incident.resolved_at.is_(None)).count(),
            "alert_channels": db.query(AlertChannel).count(),
            "alert_channels_enabled": db.query(AlertChannel).filter(AlertChannel.enabled.is_(True)).count(),
        },
        "monitoring": {
            "overdue": overdue[:50],
            "overdue_count": len(overdue),
            "never_checked": sum(1 for x in overdue if x["overdue_sec"] is None),
            "failing_channels": failing_channels,
        },
        "worker": _worker_status(),
    }


# ---------------- Activity feed & certificates ----------------


@router.get("/events")
def list_events(
    limit: int = 100,
    type: str | None = None,
    page_id: int | None = None,
    db: Session = Depends(get_db),
):
    """Recent probe change events (IP / certificate / content changed, cert expiring),
    newest first — the admin activity feed. Scoped to one page when page_id is given,
    otherwise across all probes."""
    limit = max(1, min(500, limit))
    q = (
        db.query(ProbeEvent, Probe, Server, Service, Page)
        .join(Probe, ProbeEvent.probe_id == Probe.id)
        .join(Server, Probe.server_id == Server.id)
        .join(Service, Server.service_id == Service.id)
        .join(Page, Service.page_id == Page.id)
        .order_by(ProbeEvent.created_at.desc())
    )
    if type:
        q = q.filter(ProbeEvent.type == type)
    if page_id is not None:
        q = q.filter(Page.id == page_id)
    return [
        {
            "id": ev.id, "type": ev.type, "detail": ev.detail,
            "created_at": ev.created_at.isoformat(),
            "probe_id": ev.probe_id, "probe_name": pr.name, "probe_type": pr.type,
            "server_name": srv.name, "server_host": srv.host,
            "service_name": svc.name, "page_title": pg.title, "page_slug": pg.slug,
        }
        for ev, pr, srv, svc, pg in q.limit(limit).all()
    ]


@router.get("/certs")
def list_certs(page_id: int | None = None, db: Session = Depends(get_db)):
    """All probes tracking a TLS certificate, soonest-expiring first — the expiry
    board. Populated from the denormalised certificate metadata on each probe.
    Scoped to one page when page_id is given, otherwise across all pages."""
    q = (
        db.query(Probe, Server, Service, Page)
        .join(Server, Probe.server_id == Server.id)
        .join(Service, Server.service_id == Service.id)
        .join(Page, Service.page_id == Page.id)
        .filter(Probe.tls_expires_at.isnot(None))
    )
    if page_id is not None:
        q = q.filter(Page.id == page_id)
    return [
        {
            "probe_id": pr.id, "probe_name": pr.name, "probe_type": pr.type,
            "status": pr.last_status,
            "server_name": srv.name, "server_host": srv.host,
            "service_name": svc.name, "page_title": pg.title, "page_slug": pg.slug,
            "expires_at": pr.tls_expires_at.isoformat() if pr.tls_expires_at else None,
            "issuer": pr.tls_issuer, "subject": pr.tls_subject,
        }
        for pr, srv, svc, pg in q.order_by(Probe.tls_expires_at.asc()).all()
    ]


# ---------------- Global search ----------------


@router.get("/search")
def admin_search(q: str = "", db: Session = Depends(get_db)):
    """Find probes (by name) and servers (by name or host) across all pages, with
    their page context — powers the admin quick-find. Needs at least 2 chars."""
    q = (q or "").strip()
    if len(q) < 2:
        return {"probes": [], "servers": []}
    like = f"%{q}%"

    probes = [
        {
            "probe_id": r.id, "probe_name": r.name, "probe_type": r.type,
            "server_name": r.server_name, "server_host": r.host, "service_name": r.service_name,
            "page_id": r.page_id, "page_slug": r.slug, "page_title": r.page_title,
        }
        for r in (
            db.query(
                Probe.id, Probe.name, Probe.type, Server.name.label("server_name"),
                Server.host, Service.name.label("service_name"),
                Page.id.label("page_id"), Page.slug, Page.title.label("page_title"),
            )
            .join(Server, Probe.server_id == Server.id)
            .join(Service, Server.service_id == Service.id)
            .join(Page, Service.page_id == Page.id)
            .filter(Probe.name.ilike(like))
            .order_by(Probe.name)
            .limit(25)
            .all()
        )
    ]
    servers = [
        {
            "server_id": r.id, "server_name": r.name, "server_host": r.host,
            "service_name": r.service_name, "page_id": r.page_id, "page_slug": r.slug,
            "page_title": r.page_title,
        }
        for r in (
            db.query(
                Server.id, Server.name, Server.host, Service.name.label("service_name"),
                Page.id.label("page_id"), Page.slug, Page.title.label("page_title"),
            )
            .join(Service, Server.service_id == Service.id)
            .join(Page, Service.page_id == Page.id)
            .filter(or_(Server.name.ilike(like), Server.host.ilike(like)))
            .order_by(Server.name)
            .limit(25)
            .all()
        )
    ]
    return {"probes": probes, "servers": servers}


# ---------------- Alert channels ----------------

# Secret config keys per channel type — masked in responses, preserved when the
# client submits an empty value on update (so they aren't accidentally wiped).
_SECRET_KEYS = {"telegram": ["bot_token", "webhook_secret"], "webhook": ["secret_value"], "email": ["password"]}
_MASK = "••••••"


def _masked(channel: AlertChannel) -> AlertChannelOut:
    cfg = dict(channel.config or {})
    for key in _SECRET_KEYS.get(channel.type, []):
        if cfg.get(key):
            cfg[key] = _MASK
    return AlertChannelOut(
        id=channel.id, name=channel.name, type=channel.type, enabled=channel.enabled,
        page_id=channel.page_id, escalate_after_min=channel.escalate_after_min, config=cfg,
        last_sent_at=channel.last_sent_at, last_ok=channel.last_ok, last_error=channel.last_error,
    )


@router.get("/alert-settings", response_model=AlertSettingsOut)
def get_alert_settings(db: Session = Depends(get_db)):
    row = db.get(AppSettings, 1)
    return AlertSettingsOut(
        alert_storm_window_sec=get_alert_storm_window_sec(db),
        alert_storm_window_sec_default=settings.alert_storm_window_sec,
        alert_storm_window_sec_overridden=row is not None and row.alert_storm_window_sec is not None,
    )


@router.put("/alert-settings", response_model=AlertSettingsOut)
def update_alert_settings(payload: AlertSettingsUpdate, db: Session = Depends(get_db)):
    row = db.get(AppSettings, 1)
    if row is None:
        row = AppSettings(id=1)
        db.add(row)
    row.alert_storm_window_sec = payload.alert_storm_window_sec
    db.commit()
    return get_alert_settings(db)


@router.get("/alert-channels", response_model=list[AlertChannelOut])
def list_alert_channels(db: Session = Depends(get_db)):
    return [_masked(c) for c in db.query(AlertChannel).order_by(AlertChannel.id).all()]


@router.post("/alert-channels/{channel_id}/menu")
def toggle_alert_channel_menu(channel_id: int, payload: AlertChannelMenuToggle, db: Session = Depends(get_db)):
    """Enable/disable the /menu bot commands for a Telegram channel by
    registering (or removing) its inbound webhook. Note: enabling this makes
    Telegram stop serving getUpdates for the bot (used by "pick chat_id") —
    set chat_id before turning /menu on."""
    channel = _get_or_404(db, AlertChannel, channel_id)
    if channel.type != "telegram":
        raise HTTPException(status_code=400, detail="menu commands are telegram-only")
    cfg = dict(channel.config or {})
    if payload.enabled:
        if not settings.public_base_url:
            raise HTTPException(status_code=400, detail="PUBLIC_BASE_URL not configured on the server")
        if not cfg.get("bot_token") or not cfg.get("chat_id"):
            raise HTTPException(status_code=400, detail="bot_token and chat_id required first")
        secret = cfg.get("webhook_secret") or uuid4().hex
        url = f"{settings.public_base_url.rstrip('/')}/api/telegram/webhook/{channel.id}/{secret}"
        try:
            set_telegram_webhook(cfg, url)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"setWebhook failed: {exc}")
        cfg["menu_enabled"] = True
        cfg["webhook_secret"] = secret
    else:
        delete_telegram_webhook(cfg)
        cfg["menu_enabled"] = False
    channel.config = cfg
    db.commit()
    return {"ok": True, "menu_enabled": cfg["menu_enabled"]}


def _resolve_test_config(payload: AlertChannelTest, db: Session) -> dict:
    """Merge submitted config with stored secrets (when fields were left blank or
    masked) so tests work for an existing channel without re-entering secrets."""
    cfg = dict(payload.config or {})
    if payload.channel_id is not None:
        stored = db.get(AlertChannel, payload.channel_id)
        if stored is not None:
            # Fill any field left blank or masked from the stored config so a
            # test for an existing channel needs no secret re-entry (any type).
            for key, val in (stored.config or {}).items():
                if not cfg.get(key) or cfg.get(key) == _MASK:
                    cfg[key] = val
    return cfg


@router.post("/alert-channels/test")
def test_alert_channel(payload: AlertChannelTest, db: Session = Depends(get_db)):
    """Test a channel's connectivity without sending a real alert:
    Telegram getMe (optional SOCKS5 proxy), a webhook ping POST, or an SMTP
    handshake + login."""
    cfg = _resolve_test_config(payload, db)
    if payload.type == "telegram":
        ok, detail = test_telegram(cfg.get("bot_token"), cfg.get("proxy"))
    elif payload.type == "webhook":
        ok, detail = test_webhook(cfg)
    elif payload.type == "email":
        ok, detail = test_email(cfg)
    else:
        return {"ok": False, "detail": "test not supported for this channel type"}
    return {"ok": ok, "detail": detail}


@router.post("/alert-channels/test-alert")
def test_alert_channel_send(payload: AlertChannelTest, db: Session = Depends(get_db)):
    """Send a real sample alert through the channel (Telegram / webhook / email)
    so the operator can confirm end-to-end delivery."""
    cfg = _resolve_test_config(payload, db)
    if payload.type == "telegram":
        ok, detail = send_test_telegram(cfg)
    elif payload.type == "webhook":
        ok, detail = send_test_webhook(cfg)
    elif payload.type == "email":
        ok, detail = send_test_email(cfg)
    else:
        return {"ok": False, "detail": "test alert not supported for this channel type"}
    # A manual test alert is a real delivery — surface its outcome in the list too.
    if payload.channel_id is not None:
        record_deliveries([(payload.channel_id, ok, None if ok else detail)])
    return {"ok": ok, "detail": detail}


@router.post("/alert-channels/preview")
def preview_alert_channel(payload: AlertChannelTest):
    """Render what the channel will send for a sample incident (live template
    preview). No delivery, no DB access."""
    return render_preview(payload.type, payload.config or {})


@router.post("/alert-channels/telegram-chats")
def telegram_chats(payload: AlertChannelTest, db: Session = Depends(get_db)):
    """List chats the Telegram bot can see (via getUpdates) so the operator can
    pick a chat_id instead of looking it up manually."""
    cfg = _resolve_test_config(payload, db)
    ok, result = list_telegram_chats(cfg.get("bot_token"), cfg.get("proxy"))
    if ok:
        return {"ok": True, "chats": result}
    return {"ok": False, "detail": result}


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
