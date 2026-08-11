from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

ALERT_TYPES = ("telegram", "webhook", "email")


class AlertChannel(Base):
    __tablename__ = "alert_channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Routing: null = all pages; otherwise only alerts for this page.
    page_id: Mapped[int | None] = mapped_column(
        ForeignKey("pages.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # 0 = normal channel (alerts immediately). >0 = escalation channel: only
    # notified when an incident stays open at least this many minutes.
    escalate_after_min: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # telegram {bot_token, chat_id}; webhook {url, secret_header, secret_value}
    config: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Last delivery outcome (real alerts + manual "test alert") — surfaced in the
    # admin UI so broken channels are visible without a manual probe.
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class AppSettings(Base):
    """Singleton row (id=1) of admin-editable overrides for tunables that
    otherwise only live in app/config.py (env vars, requires a restart to
    change). A NULL column means "no override, use the env default" — see
    app/alerts/dispatcher.get_alert_storm_window_sec()."""
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    # NULL = use settings.alert_storm_window_sec (env default). 0 = disabled.
    alert_storm_window_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
