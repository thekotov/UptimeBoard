from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# Status levels used across the aggregation hierarchy.
STATUS_UP = "up"
STATUS_DEGRADED = "degraded"
STATUS_DOWN = "down"
STATUS_UNKNOWN = "unknown"
STATUS_PAUSED = "paused"  # probe disabled/paused — excluded from aggregation

PROBE_TYPES = ("icmp", "tcp", "http", "tls", "heartbeat")


class Page(Base):
    __tablename__ = "pages"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Optional one-level folder/group label for organising many pages.
    group_name: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    is_published: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Private pages are hidden from the public directory but still reachable
    # via their direct /status/<slug> link.
    is_private: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Default display density on the public page: when True, server blocks start
    # collapsed (click to reveal probes); when False, everything is expanded.
    default_collapsed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    services: Mapped[list["Service"]] = relationship(
        back_populates="page",
        cascade="all, delete-orphan",
        order_by="Service.order, Service.id",
    )


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(primary_key=True)
    page_id: Mapped[int] = mapped_column(
        ForeignKey("pages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    page: Mapped["Page"] = relationship(back_populates="services")
    servers: Mapped[list["Server"]] = relationship(
        back_populates="service",
        cascade="all, delete-orphan",
        order_by="Server.order, Server.id",
    )


class Server(Base):
    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(primary_key=True)
    service_id: Mapped[int] = mapped_column(
        ForeignKey("services.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    service: Mapped["Service"] = relationship(back_populates="servers")
    probes: Mapped[list["Probe"]] = relationship(
        back_populates="server",
        cascade="all, delete-orphan",
        order_by="Probe.order, Probe.id",
    )


class Probe(Base):
    __tablename__ = "probes"

    id: Mapped[int] = mapped_column(primary_key=True)
    server_id: Mapped[int] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    interval_sec: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    timeout_sec: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # type-specific settings: http {url, method, expected_status, expected_body_substr,
    # follow_redirects}; tcp {port}; icmp {count, packet_size}; tls {port, warn_days}
    config: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    # Open an incident only after this many consecutive bad checks (flap guard).
    failure_threshold: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # Optional: mark "degraded" when latency exceeds this (ms), even if reachable.
    latency_degraded_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Visual tiering of a failing probe by consecutive failures: it shows as
    # "degraded" once it has failed this many times in a row, and as "down" once
    # it reaches `down_threshold`. With both at 1 a failure is "down" immediately.
    degraded_threshold: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    down_threshold: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Denormalised latest result (kept in sync by the worker) for fast snapshots
    # and live status in the admin UI without scanning probe_results.
    last_status: Mapped[str] = mapped_column(String(16), default=STATUS_UNKNOWN, nullable=False)
    last_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # For heartbeat probes: timestamp of the last external ping received.
    last_ping_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    server: Mapped["Server"] = relationship(back_populates="probes")
    results: Mapped[list["ProbeResult"]] = relationship(
        back_populates="probe",
        cascade="all, delete-orphan",
    )
    incidents: Mapped[list["Incident"]] = relationship(
        back_populates="probe",
        cascade="all, delete-orphan",
    )


class ProbeResult(Base):
    __tablename__ = "probe_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    probe_id: Mapped[int] = mapped_column(
        ForeignKey("probes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    probe: Mapped["Probe"] = relationship(back_populates="results")


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(primary_key=True)
    probe_id: Mapped[int] = mapped_column(
        ForeignKey("probes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str] = mapped_column(String(16), nullable=False)
    # When we last sent a notification for this incident (for repeat reminders).
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # When this incident was escalated to escalation channels (once).
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # When an operator acknowledged this incident; while set, repeat/escalation
    # alerts are suppressed (the resolved alert still fires).
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    probe: Mapped["Probe"] = relationship(back_populates="incidents")


class MaintenanceWindow(Base):
    __tablename__ = "maintenance_windows"

    id: Mapped[int] = mapped_column(primary_key=True)
    page_id: Mapped[int] = mapped_column(
        ForeignKey("pages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Announcement(Base):
    """Informational banner shown on a public page for a time window. Unlike a
    maintenance window it does NOT suppress alerts — it's purely a message."""

    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(primary_key=True)
    page_id: Mapped[int] = mapped_column(
        ForeignKey("pages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    level: Mapped[str] = mapped_column(String(16), default="info", nullable=False)  # info | warning
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
