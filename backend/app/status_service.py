"""Build status snapshots for a page from the denormalised probe status."""

import ipaddress
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session, selectinload

from app.aggregation import aggregate, worst
from app.config import settings
from app.models.monitoring import (
    STATUS_PAUSED,
    STATUS_RECOVERED,
    STATUS_UNKNOWN,
    STATUS_UP,
    Announcement,
    Incident,
    MaintenanceWindow,
    Page,
    Probe,
    Server,
    Service,
)
from app.schemas.public import (
    AnnouncementInfo,
    MaintenanceInfo,
    PageStatus,
    ProbeStatus,
    ServerStatus,
    ServiceStatus,
)


def mask_host(host: str) -> str:
    """Hide the last segment of an IP address for public display
    (e.g. ``22.33.22.11`` → ``22.33.22.**``). Hostnames are returned unchanged."""
    h = host.strip()
    try:
        ipaddress.IPv4Address(h)
        return ".".join(h.split(".")[:-1] + ["**"])
    except ValueError:
        pass
    try:
        ipaddress.IPv6Address(h)
        head, _, _ = h.rpartition(":")
        return f"{head}:**" if head else "**"
    except ValueError:
        pass
    return host


def _is_stale(probe: Probe, now: datetime) -> bool:
    """A probe is stale if it hasn't been checked within several intervals
    (e.g. the worker stopped) — its status can no longer be trusted."""
    if probe.last_checked_at is None:
        return True
    max_age = probe.interval_sec * settings.stale_factor + settings.stale_grace_sec
    return (now - probe.last_checked_at).total_seconds() > max_age


def _recently_recovered_probe_ids(
    db: Session, probe_ids: list[int], now: datetime
) -> set[int]:
    """Probe ids that had an incident resolve within the recovery window.

    Such probes, if currently up, are shown as "recovered" so a recent outage
    stays visible for a while after everything is green again."""
    if not probe_ids or settings.recovery_window_sec <= 0:
        return set()
    cutoff = now - timedelta(seconds=settings.recovery_window_sec)
    rows = (
        db.query(Incident.probe_id)
        .filter(
            Incident.probe_id.in_(probe_ids),
            Incident.resolved_at.isnot(None),
            Incident.resolved_at >= cutoff,
        )
        .distinct()
        .all()
    )
    return {pid for (pid,) in rows}


def build_page_status(db: Session, page: Page) -> PageStatus:
    page = (
        db.query(Page)
        .options(
            selectinload(Page.services)
            .selectinload(Service.servers)
            .selectinload(Server.probes)
        )
        .filter(Page.id == page.id)
        .one()
    )
    now = datetime.now(timezone.utc)

    all_probe_ids = [
        probe.id
        for service in page.services
        for server in service.servers
        for probe in server.probes
    ]
    recovered_ids = _recently_recovered_probe_ids(db, all_probe_ids, now)

    service_statuses: list[ServiceStatus] = []
    for service in page.services:
        server_statuses: list[ServerStatus] = []
        for server in service.servers:
            probe_statuses: list[ProbeStatus] = []
            for probe in server.probes:
                if not probe.enabled:
                    # Paused probe: shown but excluded from the server's status.
                    probe_statuses.append(
                        ProbeStatus(
                            id=probe.id, name=probe.name, type=probe.type,
                            status=STATUS_PAUSED, latency_ms=probe.last_latency_ms,
                            error=None, checked_at=probe.last_checked_at, stale=False,
                        )
                    )
                    continue
                stale = _is_stale(probe, now)
                # A stale probe's last status is no longer trustworthy → unknown.
                status = STATUS_UNKNOWN if stale else probe.last_status
                # Up again, but an incident resolved within the recovery window:
                # celebrate it as "recovered" until the window elapses.
                if status == STATUS_UP and probe.id in recovered_ids:
                    status = STATUS_RECOVERED
                probe_statuses.append(
                    ProbeStatus(
                        id=probe.id,
                        name=probe.name,
                        type=probe.type,
                        status=status,
                        latency_ms=probe.last_latency_ms,
                        error=probe.last_error,
                        checked_at=probe.last_checked_at,
                        stale=stale,
                    )
                )
            # Paused probes do not count toward the server's aggregated status.
            server_status = worst([ps.status for ps in probe_statuses if ps.status != STATUS_PAUSED])
            server_statuses.append(
                ServerStatus(
                    id=server.id,
                    name=server.name,
                    host=mask_host(server.host) if page.mask_ip else server.host,
                    note=server.note,
                    status=server_status,
                    probes=probe_statuses,
                )
            )
        service_status = aggregate([ss.status for ss in server_statuses])
        service_statuses.append(
            ServiceStatus(
                id=service.id,
                name=service.name,
                status=service_status,
                servers=server_statuses,
            )
        )

    overall = aggregate([ss.status for ss in service_statuses])

    window = (
        db.query(MaintenanceWindow)
        .filter(
            MaintenanceWindow.page_id == page.id,
            MaintenanceWindow.starts_at <= now,
            MaintenanceWindow.ends_at >= now,
        )
        .order_by(MaintenanceWindow.ends_at.desc())
        .first()
    )
    maintenance = (
        MaintenanceInfo(title=window.title, starts_at=window.starts_at, ends_at=window.ends_at)
        if window
        else None
    )

    announcements = [
        AnnouncementInfo(title=a.title, body=a.body, level=a.level, starts_at=a.starts_at, ends_at=a.ends_at)
        for a in (
            db.query(Announcement)
            .filter(
                Announcement.page_id == page.id,
                Announcement.starts_at <= now,
                Announcement.ends_at >= now,
            )
            .order_by(Announcement.starts_at.desc())
            .all()
        )
    ]

    return PageStatus(
        slug=page.slug,
        title=page.title,
        description=page.description,
        group_name=page.group_name,
        status=overall,
        default_collapsed=page.default_collapsed,
        generated_at=now,
        maintenance=maintenance,
        announcements=announcements,
        services=service_statuses,
    )


def find_page_for_probe(db: Session, probe_id: int) -> Page | None:
    """Resolve the owning page of a probe (for targeted real-time updates)."""
    return (
        db.query(Page)
        .join(Service, Service.page_id == Page.id)
        .join(Server, Server.service_id == Service.id)
        .join(Probe, Probe.server_id == Server.id)
        .filter(Probe.id == probe_id)
        .first()
    )
