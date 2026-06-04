from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database / cache
    database_url: str = "postgresql+psycopg2://monitor:monitor@postgres:5432/monitor"
    redis_url: str = "redis://redis:6379/0"
    # SQLAlchemy connection pool. Peak connections per process = pool_size +
    # db_max_overflow. Keep (api + worker) totals under Postgres' max_connections.
    # Size db_pool_size/db_max_overflow to the worker thread pool so probe checks
    # don't queue waiting for a connection.
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: int = 30

    # Auth
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_min: int = 720

    # Seed admin (used by scripts/seed.py)
    admin_email: str = "admin@example.com"
    admin_password: str = "admin"

    # Probes / retention
    result_retention_days: int = 30  # raw probe_results kept this long
    rollup_retention_days: int = 400  # aggregated probe_rollups kept this long
    rollup_interval_min: int = 15  # how often the rollup job runs
    probe_default_interval: int = 60
    probe_default_timeout: int = 10
    # Delay between in-check retries (see Probe.retries). A failed check is retried
    # after this pause before being accepted, to absorb transient single blips.
    probe_retry_backoff_sec: float = 2.0

    # Uplink self-check (anti-storm). Before trusting a *failed* check, the worker
    # verifies it has network itself by TCP-connecting to a few stable anchors. If
    # ALL anchors are unreachable we assume the monitor is offline (not the targets)
    # and record the check as "unknown" with alerts suppressed — preventing a whole
    # dashboard from alarming on the monitor's own connectivity blip.
    uplink_check_enabled: bool = True
    uplink_anchors: str = "1.1.1.1:443,8.8.8.8:443,9.9.9.9:443"
    uplink_timeout_sec: float = 3.0
    uplink_cache_sec: float = 5.0

    # Worker: size of the thread pool that runs probe checks concurrently.
    # Probes are I/O-bound (network waits), so this can comfortably exceed cores.
    worker_max_threads: int = 50

    # Re-send an alert if an incident is still open after this many minutes (0 = off).
    alert_repeat_min: int = 0
    # Coalesce "down"/"resolved" alerts for the same server within this window so a
    # whole-server outage sends one alert instead of one per probe (0 = off).
    alert_group_window_sec: int = 60
    # A probe whose last check is older than interval * factor + grace is "stale".
    stale_factor: int = 3
    stale_grace_sec: int = 30

    # After an incident is resolved, a probe/server/service that is back up keeps
    # a celebratory "recovered" status for this long (so a recent outage stays
    # visible even after everything is green again). Default: 1 hour.
    recovery_window_sec: int = 3600

    # Alert delivery: retry a failed send (network blip, 5xx from Telegram/SMTP)
    # this many times with exponential backoff before giving up.
    alert_retry_attempts: int = 3
    alert_retry_backoff_sec: float = 2.0

    # Worker liveness (dead-man switch): the worker writes a heartbeat every
    # heartbeat_sec; /api/health/worker reports unhealthy if the last beat is
    # older than stale_sec (lets an external uptime check detect a dead worker).
    worker_heartbeat_sec: int = 30
    worker_stale_sec: int = 120

    # Redis channels / keys
    redis_status_channel: str = "status_updates"
    redis_worker_heartbeat_key: str = "worker:heartbeat"

    # CORS (comma-separated origins; "*" allows all)
    cors_origins: str = "*"

    # Public base URL of the status site (e.g. https://uptime.example.com), used
    # to build clickable links in alerts. Empty = omit links.
    public_base_url: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
