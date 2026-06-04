"""Probe scheduler/worker.

Runs each enabled probe on its own interval via APScheduler. For every check it
records a ProbeResult, opens/closes incidents on status transitions (with
deduplicated alerts), publishes a real-time update to Redis, and periodically
prunes old results.

A separate thread listens on a Redis channel for "reload" signals emitted by the
admin API when probes change, and rebuilds the schedule.
"""

import logging
import threading
from datetime import datetime, timedelta, timezone

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import Probe, ProbeResult
from app.probe_runner import execute_and_store
from app.realtime import get_sync_redis, write_worker_heartbeat
from app.rollups import backfill_rollups, build_rollups, cleanup_rollups

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("worker")

# A larger thread pool lets many I/O-bound probes run concurrently; misfire
# grace + coalesce keep things sane if checks pile up under load.
scheduler = BackgroundScheduler(
    executors={"default": ThreadPoolExecutor(max_workers=settings.worker_max_threads)},
    job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 30},
)


def _check_probe(probe_id: int) -> None:
    """Run a single probe check (called by the scheduler)."""
    db: Session = SessionLocal()
    try:
        probe = db.get(Probe, probe_id)
        if probe is None or not probe.enabled:
            return
        execute_and_store(db, probe)
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("probe %s check failed", probe_id)
    finally:
        db.close()


def _retention_cleanup() -> None:
    # Roll up first so freshly-aggregated history survives the raw cleanup.
    db: Session = SessionLocal()
    try:
        build_rollups(db)
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("rollup build failed (skipping raw cleanup this tick)")
        db.close()
        return
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=settings.result_retention_days)
        deleted = (
            db.query(ProbeResult).filter(ProbeResult.checked_at < cutoff).delete(synchronize_session=False)
        )
        db.commit()
        if deleted:
            logger.info("retention: removed %s old probe results", deleted)
        dropped = cleanup_rollups(db)
        if dropped:
            logger.info("retention: removed %s old rollups", dropped)
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("retention cleanup failed")
    finally:
        db.close()


def _rollup_tick() -> None:
    """Frequent rollup refresh so long-range views stay current between the
    less-frequent retention runs."""
    db: Session = SessionLocal()
    try:
        build_rollups(db)
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("rollup tick failed")
    finally:
        db.close()


def _load_schedule() -> None:
    """(Re)build scheduler jobs from the enabled probes in the database."""
    db: Session = SessionLocal()
    try:
        probes = db.query(Probe).filter(Probe.enabled.is_(True)).all()
        wanted_ids = {p.id for p in probes}

        # Remove jobs for probes that are gone/disabled.
        for job in scheduler.get_jobs():
            if job.id.startswith("probe-"):
                pid = int(job.id.split("-", 1)[1])
                if pid not in wanted_ids:
                    scheduler.remove_job(job.id)

        # Add/update jobs for current probes.
        for probe in probes:
            scheduler.add_job(
                _check_probe,
                trigger="interval",
                seconds=probe.interval_sec,
                args=[probe.id],
                id=f"probe-{probe.id}",
                replace_existing=True,
                next_run_time=datetime.now(timezone.utc),  # run once immediately
                max_instances=1,
                coalesce=True,
            )
        logger.info("schedule loaded: %s enabled probes", len(probes))
    finally:
        db.close()


def _listen_for_reload() -> None:
    """Background thread: reload the schedule when the API signals a change."""
    channel = settings.redis_status_channel + ":reload"
    pubsub = get_sync_redis().pubsub()
    pubsub.subscribe(channel)
    for message in pubsub.listen():
        if message.get("type") == "message":
            logger.info("reload signal received; rebuilding schedule")
            try:
                _load_schedule()
            except Exception:  # noqa: BLE001
                logger.exception("schedule reload failed")


def main() -> None:
    logger.info("starting monitoring worker")
    _load_schedule()
    # One-time backfill so long-range views are populated from existing raw data.
    db = SessionLocal()
    try:
        backfill_rollups(db)
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("initial rollup backfill failed")
    finally:
        db.close()
    write_worker_heartbeat()  # beat once at startup so health is green immediately
    scheduler.add_job(
        write_worker_heartbeat,
        trigger="interval",
        seconds=settings.worker_heartbeat_sec,
        id="worker-heartbeat",
        replace_existing=True,
    )
    scheduler.add_job(
        _retention_cleanup,
        trigger="interval",
        hours=6,
        id="retention-cleanup",
        replace_existing=True,
    )
    scheduler.add_job(
        _rollup_tick,
        trigger="interval",
        minutes=settings.rollup_interval_min,
        id="rollup-tick",
        replace_existing=True,
        next_run_time=datetime.now(timezone.utc),  # build an initial set immediately
    )
    # Periodic safety-net reload: picks up probes added directly to the DB
    # (e.g. the seed script) or any reload signal that was missed.
    scheduler.add_job(
        _load_schedule,
        trigger="interval",
        seconds=30,
        id="schedule-reload",
        replace_existing=True,
    )
    scheduler.start()

    listener = threading.Thread(target=_listen_for_reload, daemon=True)
    listener.start()

    try:
        listener.join()
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()


if __name__ == "__main__":
    main()
