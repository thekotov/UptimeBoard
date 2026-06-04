"""Metric rollups: pre-aggregate raw probe_results into compact per-period rows.

Raw results are high-volume (a probe every 8-25s) and only kept for a short
retention window. Rollups (one row per probe per hour/day) hold uptime %, mean
and p50/p95/p99/min/max latency computed over the raw checks of that period, so
long-range dashboards stay fast and keep working after raw cleanup.

The aggregation runs entirely in Postgres (``percentile_cont`` for exact
percentiles) and is upserted, so re-running over the same window is idempotent —
we simply recompute the last few periods each tick to catch in-progress and
late-arriving data.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger("rollups")

# date_trunc field per period, and how far back to recompute each run.
_PERIODS = {
    "hour": ("hour", timedelta(hours=3)),
    "day": ("day", timedelta(days=2)),
}

_UPSERT = text(
    """
    INSERT INTO probe_rollups
      (probe_id, period, bucket, total, up_count, degraded_count, down_count,
       uptime_pct, latency_avg, latency_p50, latency_p95, latency_p99,
       latency_min, latency_max)
    SELECT
      probe_id,
      :period,
      date_trunc(:trunc, checked_at),
      count(*),
      count(*) FILTER (WHERE status = 'up'),
      count(*) FILTER (WHERE status = 'degraded'),
      count(*) FILTER (WHERE status = 'down'),
      CASE WHEN count(*) > 0
           THEN 100.0 * count(*) FILTER (WHERE status = 'up') / count(*)
           ELSE 0 END,
      avg(latency_ms),
      percentile_cont(0.5)  WITHIN GROUP (ORDER BY latency_ms),
      percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms),
      percentile_cont(0.99) WITHIN GROUP (ORDER BY latency_ms),
      min(latency_ms),
      max(latency_ms)
    FROM probe_results
    WHERE checked_at >= :since AND status <> 'maintenance'
    GROUP BY probe_id, date_trunc(:trunc, checked_at)
    ON CONFLICT (probe_id, period, bucket) DO UPDATE SET
      total          = EXCLUDED.total,
      up_count       = EXCLUDED.up_count,
      degraded_count = EXCLUDED.degraded_count,
      down_count     = EXCLUDED.down_count,
      uptime_pct     = EXCLUDED.uptime_pct,
      latency_avg    = EXCLUDED.latency_avg,
      latency_p50    = EXCLUDED.latency_p50,
      latency_p95    = EXCLUDED.latency_p95,
      latency_p99    = EXCLUDED.latency_p99,
      latency_min    = EXCLUDED.latency_min,
      latency_max    = EXCLUDED.latency_max
    """
)


def build_rollups(db: Session) -> None:
    """(Re)compute hourly and daily rollups for the most recent periods."""
    now = datetime.now(timezone.utc)
    for period, (trunc, lookback) in _PERIODS.items():
        db.execute(_UPSERT, {"period": period, "trunc": trunc, "since": now - lookback})
    db.commit()


def backfill_rollups(db: Session) -> None:
    """One-time wider pass (run at startup) so existing raw history immediately
    populates long-range views, instead of only filling in going forward.

    Bounded by raw retention — there's no raw older than that to aggregate."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=settings.result_retention_days + 1)
    for period, (trunc, _) in _PERIODS.items():
        db.execute(_UPSERT, {"period": period, "trunc": trunc, "since": since})
    db.commit()
    logger.info("rollups backfilled from raw history")


def cleanup_rollups(db: Session) -> int:
    """Drop rollups older than the configured retention window."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.rollup_retention_days)
    deleted = db.execute(
        text("DELETE FROM probe_rollups WHERE bucket < :cutoff"), {"cutoff": cutoff}
    ).rowcount
    db.commit()
    return deleted
