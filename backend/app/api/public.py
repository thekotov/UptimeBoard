import asyncio
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import asc
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from app.aggregation import worst
from app.config import settings
from app.db import SessionLocal, get_db
from app.models import Incident, Page, Probe, ProbeResult, Server, Service
from app.realtime import get_async_redis
from app.schemas.public import (
    IncidentItem,
    PageListItem,
    PageStatus,
    ProbeHistory,
    ProbeUptime,
)
from app.status_service import build_page_status

router = APIRouter(prefix="/api/public", tags=["public"])

_RANGES = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
}

# Max timeline cells returned per probe; raw results are bucketed into this many
# slots so the payload stays bounded regardless of probe frequency.
_TIMELINE_BUCKETS = 90


def _series_points(results: list, since: datetime, window: timedelta, n_buckets: int) -> list[dict]:
    """Build timeline series points for a single probe's ordered results.

    When a probe has fewer checks than display cells, emit one point per check
    so a recent recovery shows up immediately (green on the right) instead of
    being hidden inside one `worst()`-coloured bucket. Only once there are more
    checks than cells do we downsample into fixed-width buckets to keep the
    payload bounded.
    """
    if len(results) <= n_buckets:
        return [
            {
                "checked_at": r.checked_at.isoformat(),
                "status": r.status,
                "latency_ms": r.latency_ms,
            }
            for r in results
        ]

    bucket_width = window / n_buckets
    buckets: dict[int, list] = {}
    for r in results:
        idx = max(0, min(n_buckets - 1, int((r.checked_at - since) / bucket_width)))
        buckets.setdefault(idx, []).append(r)
    points: list[dict] = []
    for idx in sorted(buckets):
        group = buckets[idx]
        latencies = [g.latency_ms for g in group if g.latency_ms is not None]
        points.append(
            {
                "checked_at": (since + bucket_width * idx).isoformat(),
                "status": worst([g.status for g in group]),
                "latency_ms": (sum(latencies) / len(latencies)) if latencies else None,
            }
        )
    return points


@router.get("/pages", response_model=list[PageListItem])
def list_public_pages(db: Session = Depends(get_db)):
    """Directory of published pages (for the public home/index).

    Private pages are excluded here but remain reachable via direct link.
    """
    return (
        db.query(Page)
        .filter(Page.is_published.is_(True), Page.is_private.is_(False))
        .order_by(Page.group_name.nulls_last(), Page.title)
        .all()
    )


def _get_published_page(db: Session, slug: str) -> Page:
    page = db.query(Page).filter(Page.slug == slug, Page.is_published.is_(True)).first()
    if page is None:
        raise HTTPException(status_code=404, detail="Page not found")
    return page


@router.get("/pages/{slug}", response_model=PageStatus)
def get_page_status(slug: str, db: Session = Depends(get_db)):
    page = _get_published_page(db, slug)
    return build_page_status(db, page)


@router.get("/pages/{slug}/timeline", response_model=list[ProbeUptime])
def get_timeline(
    slug: str,
    range: str = Query(default="24h"),
    db: Session = Depends(get_db),
):
    page = _get_published_page(db, slug)
    window = _RANGES.get(range, _RANGES["24h"])
    now = datetime.now(timezone.utc)
    since = now - window

    probe_ids = [
        row[0]
        for row in (
            db.query(Probe.id)
            .join(Server, Probe.server_id == Server.id)
            .join(Service, Server.service_id == Service.id)
            .filter(Service.page_id == page.id)
            .all()
        )
    ]
    if not probe_ids:
        return []

    rows = (
        db.query(ProbeResult)
        .filter(ProbeResult.probe_id.in_(probe_ids), ProbeResult.checked_at >= since)
        .order_by(asc(ProbeResult.checked_at))
        .all()
    )

    by_probe: dict[int, list[ProbeResult]] = {pid: [] for pid in probe_ids}
    for r in rows:
        by_probe[r.probe_id].append(r)

    result: list[ProbeUptime] = []
    for pid, results in by_probe.items():
        total = len(results)
        up = sum(1 for r in results if r.status == "up")
        uptime_pct = (up / total * 100) if total else 0.0
        points = _series_points(results, since, window, _TIMELINE_BUCKETS)
        result.append(
            ProbeUptime(probe_id=pid, uptime_pct=round(uptime_pct, 2), total=total, points=points)
        )
    return result


@router.get("/pages/{slug}/incidents", response_model=list[IncidentItem])
def get_incidents(
    slug: str,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Recent incidents (open + resolved) across the page's probes."""
    page = _get_published_page(db, slug)
    rows = (
        db.query(Incident, Probe, Server, Service)
        .join(Probe, Incident.probe_id == Probe.id)
        .join(Server, Probe.server_id == Server.id)
        .join(Service, Server.service_id == Service.id)
        .filter(Service.page_id == page.id)
        .order_by(Incident.started_at.desc())
        .limit(limit)
        .all()
    )
    items: list[IncidentItem] = []
    for inc, probe, server, service in rows:
        end = inc.resolved_at or datetime.now(timezone.utc)
        duration = int((end - inc.started_at).total_seconds())
        items.append(
            IncidentItem(
                id=inc.id,
                probe_id=probe.id,
                probe_name=probe.name,
                server_name=server.name,
                service_name=service.name,
                last_status=inc.last_status,
                started_at=inc.started_at,
                resolved_at=inc.resolved_at,
                duration_sec=duration,
                ongoing=inc.resolved_at is None,
                acknowledged_at=inc.acknowledged_at,
            )
        )
    return items


@router.get("/pages/{slug}/probes/{probe_id}/history", response_model=ProbeHistory)
def get_probe_history(
    slug: str,
    probe_id: int,
    range: str = Query(default="24h"),
    db: Session = Depends(get_db),
):
    """Detailed history for a single probe: bucketed series + recent raw checks."""
    page = _get_published_page(db, slug)
    probe = (
        db.query(Probe)
        .join(Server, Probe.server_id == Server.id)
        .join(Service, Server.service_id == Service.id)
        .filter(Probe.id == probe_id, Service.page_id == page.id)
        .first()
    )
    if probe is None:
        raise HTTPException(status_code=404, detail="Probe not found")

    window = _RANGES.get(range, _RANGES["24h"])
    now = datetime.now(timezone.utc)
    since = now - window
    n_buckets = 120

    results = (
        db.query(ProbeResult)
        .filter(ProbeResult.probe_id == probe_id, ProbeResult.checked_at >= since)
        .order_by(asc(ProbeResult.checked_at))
        .all()
    )
    total = len(results)
    up = sum(1 for r in results if r.status == "up")
    uptime_pct = (up / total * 100) if total else 0.0

    points = _series_points(results, since, window, n_buckets)

    recent = [
        {
            "checked_at": r.checked_at.isoformat(),
            "status": r.status,
            "latency_ms": r.latency_ms,
            "error": r.error,
        }
        for r in results[-25:][::-1]
    ]

    return ProbeHistory(
        probe_id=probe.id,
        name=probe.name,
        type=probe.type,
        server_name=probe.server.name,
        host=probe.server.host,
        uptime_pct=round(uptime_pct, 2),
        total=total,
        points=points,
        recent=recent,
    )


@router.get("/pages/{slug}/stream")
async def stream_status(slug: str, request: Request):
    # Validate the page exists & is published before opening the stream.
    db = SessionLocal()
    try:
        _get_published_page(db, slug)
    finally:
        db.close()

    async def event_generator():
        redis = get_async_redis()
        pubsub = redis.pubsub()
        await pubsub.subscribe(settings.redis_status_channel)
        try:
            # Initial snapshot so the client renders immediately.
            yield {"event": "snapshot", "data": json.dumps(_snapshot(slug))}
            while True:
                if await request.is_disconnected():
                    break
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=15
                )
                if message is None:
                    # Heartbeat keeps proxies from closing idle connections.
                    yield {"event": "ping", "data": "{}"}
                    continue
                try:
                    data = json.loads(message["data"])
                except (TypeError, ValueError):
                    continue
                if data.get("slug") == slug:
                    yield {"event": "update", "data": json.dumps(_snapshot(slug))}
        finally:
            await pubsub.unsubscribe(settings.redis_status_channel)
            await pubsub.aclose()
            await redis.aclose()

    return EventSourceResponse(event_generator())


def _snapshot(slug: str) -> dict:
    db = SessionLocal()
    try:
        page = _get_published_page(db, slug)
        return json.loads(build_page_status(db, page).model_dump_json())
    finally:
        db.close()
