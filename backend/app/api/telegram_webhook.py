"""Public inbound webhook for Telegram bot commands (/menu and button taps).

Not behind admin auth (Telegram calls this directly) — instead verified by the
per-channel secret embedded in the path, generated when an admin enables /menu
for that channel (see admin.py's toggle_alert_channel_menu).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AlertChannel
from app.telegram_menu import handle_update

logger = logging.getLogger("telegram_webhook")

router = APIRouter(prefix="/api/telegram", tags=["telegram"])


@router.post("/webhook/{channel_id}/{secret}")
def telegram_webhook(channel_id: int, secret: str, update: dict, db: Session = Depends(get_db)):
    channel = db.get(AlertChannel, channel_id)
    if channel is None or channel.type != "telegram":
        raise HTTPException(status_code=404)
    cfg = channel.config or {}
    if not cfg.get("menu_enabled") or not cfg.get("webhook_secret") or cfg["webhook_secret"] != secret:
        raise HTTPException(status_code=403)
    try:
        handle_update(db, channel, update)
        db.commit()
    except Exception:  # noqa: BLE001 — never let a bad update break the webhook (Telegram retries on error)
        db.rollback()
        logger.exception("telegram webhook handling failed (channel %s)", channel_id)
    return {"ok": True}
