"""Redis pub/sub helpers for pushing status updates to SSE clients.

The worker publishes a small JSON message on the status channel whenever a
probe result is recorded. The API's SSE endpoint subscribes and forwards
matching messages to connected browsers.
"""

import json
import logging
import time

import redis
import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger("realtime")

_sync_client: redis.Redis | None = None


def get_sync_redis() -> redis.Redis:
    global _sync_client
    if _sync_client is None:
        _sync_client = redis.from_url(settings.redis_url, decode_responses=True)
    return _sync_client


def publish_status_update(page_slug: str, payload: dict) -> None:
    """Publish a status update (called from the worker)."""
    message = json.dumps({"slug": page_slug, **payload})
    try:
        get_sync_redis().publish(settings.redis_status_channel, message)
    except redis.RedisError as exc:
        # Real-time delivery is best-effort; never break the probe loop, but
        # log it — silent SSE outages are otherwise invisible.
        logger.warning("status update publish failed: %s", exc)


def write_worker_heartbeat() -> None:
    """Record that the worker is alive (called periodically by the worker)."""
    try:
        # TTL well past the staleness window so a stopped worker's key expires.
        get_sync_redis().set(
            settings.redis_worker_heartbeat_key,
            str(time.time()),
            ex=settings.worker_stale_sec * 3,
        )
    except redis.RedisError as exc:
        logger.warning("worker heartbeat write failed: %s", exc)


def get_worker_heartbeat_age() -> float | None:
    """Seconds since the worker last beat, or None if no heartbeat is recorded."""
    try:
        raw = get_sync_redis().get(settings.redis_worker_heartbeat_key)
    except redis.RedisError:
        return None
    if not raw:
        return None
    try:
        return max(0.0, time.time() - float(raw))
    except (TypeError, ValueError):
        return None


def get_async_redis() -> aioredis.Redis:
    return aioredis.from_url(settings.redis_url, decode_responses=True)
