from datetime import datetime

from pydantic import BaseModel


class ProbeStatus(BaseModel):
    id: int
    name: str
    type: str
    status: str
    latency_ms: float | None = None
    error: str | None = None
    checked_at: datetime | None = None
    stale: bool = False


class MaintenanceInfo(BaseModel):
    title: str
    starts_at: datetime
    ends_at: datetime


class AnnouncementInfo(BaseModel):
    title: str
    body: str | None = None
    level: str
    starts_at: datetime
    ends_at: datetime


class ServerStatus(BaseModel):
    id: int
    name: str
    host: str
    status: str
    probes: list[ProbeStatus] = []


class ServiceStatus(BaseModel):
    id: int
    name: str
    status: str
    servers: list[ServerStatus] = []


class PageStatus(BaseModel):
    slug: str
    title: str
    description: str | None = None
    group_name: str | None = None
    status: str
    default_collapsed: bool = True
    generated_at: datetime
    maintenance: MaintenanceInfo | None = None
    announcements: list[AnnouncementInfo] = []
    services: list[ServiceStatus] = []


class PageListItem(BaseModel):
    slug: str
    title: str
    group_name: str | None = None

    model_config = {"from_attributes": True}


class ProbeUptime(BaseModel):
    probe_id: int
    uptime_pct: float
    total: int
    points: list[dict]  # [{checked_at, status, latency_ms}]


class IncidentItem(BaseModel):
    id: int
    probe_id: int
    probe_name: str
    server_name: str
    service_name: str
    last_status: str
    started_at: datetime
    resolved_at: datetime | None = None
    duration_sec: int | None = None
    ongoing: bool
    acknowledged_at: datetime | None = None


class LatencyStats(BaseModel):
    avg: float | None = None
    p50: float | None = None
    p95: float | None = None
    p99: float | None = None
    min: float | None = None
    max: float | None = None
    # True when derived from rollups (percentiles are approximate, not exact).
    approx: bool = False


class ProbeHistory(BaseModel):
    probe_id: int
    name: str
    type: str
    server_name: str
    host: str
    uptime_pct: float
    total: int
    points: list[dict]  # bucketed [{checked_at, status, latency_ms}]
    recent: list[dict]  # last raw checks [{checked_at, status, latency_ms, error}]
    latency: LatencyStats | None = None
