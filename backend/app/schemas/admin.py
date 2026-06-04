from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.alerts.dispatcher import ALERT_EVENTS


def _validate_channel_config(config: dict | None) -> dict | None:
    """Validate the optional notification-tuning keys in a channel config.

    - ``events``: list of subscribed event types (subset of ALERT_EVENTS).
      Absent/empty means "receive all events".
    - ``template``: custom message template string.
    Secrets and per-type keys (bot_token, url, ...) are left untouched.
    """
    if not config:
        return config
    events = config.get("events")
    if events is not None:
        if not isinstance(events, list) or not all(isinstance(e, str) for e in events):
            raise ValueError("config.events must be a list of strings")
        unknown = [e for e in events if e not in ALERT_EVENTS]
        if unknown:
            raise ValueError(f"config.events has unknown event(s): {unknown}; allowed: {list(ALERT_EVENTS)}")
    template = config.get("template")
    if template is not None and not isinstance(template, str):
        raise ValueError("config.template must be a string")
    return config

# ---- Probe ----


class ProbeBase(BaseModel):
    name: str
    type: Literal["icmp", "tcp", "http", "tls", "heartbeat"]
    interval_sec: int = Field(default=60, ge=5, le=86400)
    timeout_sec: int = Field(default=10, ge=1, le=300)
    enabled: bool = True
    order: int = 0
    failure_threshold: int = Field(default=1, ge=1, le=20)
    latency_degraded_ms: int | None = Field(default=None, ge=1)
    degraded_threshold: int = Field(default=1, ge=1, le=20)
    down_threshold: int = Field(default=1, ge=1, le=20)
    tolerance_checks: int = Field(default=0, ge=0, le=20)
    recovery_threshold: int = Field(default=1, ge=1, le=20)
    config: dict = {}


class ProbeCreate(ProbeBase):
    server_id: int
    # None = inherit the page's default tolerance at creation time.
    tolerance_checks: int | None = Field(default=None, ge=0, le=20)


class ProbeUpdate(BaseModel):
    name: str | None = None
    server_id: int | None = None  # allows moving a probe to another server
    interval_sec: int | None = Field(default=None, ge=5, le=86400)
    timeout_sec: int | None = Field(default=None, ge=1, le=300)
    enabled: bool | None = None
    order: int | None = None
    failure_threshold: int | None = Field(default=None, ge=1, le=20)
    latency_degraded_ms: int | None = Field(default=None, ge=1)
    degraded_threshold: int | None = Field(default=None, ge=1, le=20)
    down_threshold: int | None = Field(default=None, ge=1, le=20)
    tolerance_checks: int | None = Field(default=None, ge=0, le=20)
    recovery_threshold: int | None = Field(default=None, ge=1, le=20)
    config: dict | None = None


class ProbeOut(ProbeBase):
    id: int
    server_id: int
    # denormalised live status (read-only)
    last_status: str
    last_latency_ms: float | None = None
    last_checked_at: datetime | None = None
    last_error: str | None = None
    last_ping_at: datetime | None = None

    model_config = {"from_attributes": True}


class ProbeTest(BaseModel):
    """Run a probe configuration without persisting it (test-before-save)."""
    type: Literal["icmp", "tcp", "http", "tls", "heartbeat"]
    timeout_sec: int = Field(default=10, ge=1, le=300)
    config: dict = {}
    server_id: int | None = None
    host: str | None = None


# ---- Server ----


class ServerBase(BaseModel):
    name: str
    host: str
    order: int = 0


class ServerCreate(ServerBase):
    service_id: int


class ServerUpdate(BaseModel):
    name: str | None = None
    host: str | None = None
    order: int | None = None


class ServerOut(ServerBase):
    id: int
    service_id: int
    probes: list[ProbeOut] = []

    model_config = {"from_attributes": True}


# ---- Service ----


class ServiceBase(BaseModel):
    name: str
    order: int = 0


class ServiceCreate(ServiceBase):
    page_id: int


class ServiceUpdate(BaseModel):
    name: str | None = None
    order: int | None = None


class ServiceOut(ServiceBase):
    id: int
    page_id: int
    servers: list[ServerOut] = []

    model_config = {"from_attributes": True}


# ---- Page ----


class PageBase(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9\-]*$", max_length=128)
    title: str
    description: str | None = None
    group_name: str | None = Field(default=None, max_length=128)
    is_published: bool = True
    is_private: bool = False
    default_collapsed: bool = True
    default_tolerance_checks: int = Field(default=0, ge=0, le=20)
    mask_ip: bool = False


class PageCreate(PageBase):
    pass


class PageUpdate(BaseModel):
    slug: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9\-]*$", max_length=128)
    title: str | None = None
    description: str | None = None
    group_name: str | None = Field(default=None, max_length=128)
    is_published: bool | None = None
    is_private: bool | None = None
    default_collapsed: bool | None = None
    default_tolerance_checks: int | None = Field(default=None, ge=0, le=20)
    mask_ip: bool | None = None


class PageOut(PageBase):
    id: int

    model_config = {"from_attributes": True}


class PageDetailOut(PageOut):
    services: list[ServiceOut] = []


# ---- Alert channel ----


class AlertChannelBase(BaseModel):
    name: str
    type: Literal["telegram", "webhook", "email"]
    enabled: bool = True
    page_id: int | None = None  # null = all pages
    escalate_after_min: int = Field(default=0, ge=0, le=1440)
    config: dict = {}

    _check_config = field_validator("config")(_validate_channel_config)


class AlertChannelCreate(AlertChannelBase):
    pass


class AlertChannelUpdate(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    page_id: int | None = None
    escalate_after_min: int | None = Field(default=None, ge=0, le=1440)
    config: dict | None = None

    _check_config = field_validator("config")(_validate_channel_config)


class AlertChannelOut(AlertChannelBase):
    id: int

    model_config = {"from_attributes": True}


class AlertChannelTest(BaseModel):
    type: Literal["telegram", "webhook", "email"]
    config: dict = {}
    channel_id: int | None = None  # to reuse stored secrets when testing an existing channel


# ---- Maintenance windows ----


class MaintenanceCreate(BaseModel):
    page_id: int
    title: str
    starts_at: datetime
    ends_at: datetime


class MaintenanceOut(BaseModel):
    id: int
    page_id: int
    title: str
    starts_at: datetime
    ends_at: datetime

    model_config = {"from_attributes": True}


# ---- Announcements ----


class AnnouncementCreate(BaseModel):
    page_id: int
    title: str
    body: str | None = None
    level: Literal["info", "warning"] = "info"
    starts_at: datetime
    ends_at: datetime


class AnnouncementOut(BaseModel):
    id: int
    page_id: int
    title: str
    body: str | None = None
    level: str
    starts_at: datetime
    ends_at: datetime

    model_config = {"from_attributes": True}
