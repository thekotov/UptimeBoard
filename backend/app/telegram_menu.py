"""Interactive Telegram bot: /menu command + inline-keyboard callback router.

A Telegram channel with menu_enabled registers a webhook (see
app/api/telegram_webhook.py); Telegram then pushes every message/button-tap
from that chat here. Report builders reuse the same DB shape as the public
status/timeline endpoints (Probe -> Server -> Service -> Page) and the admin
worker-health check, rather than re-deriving that logic.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.alerts.dispatcher import (
    answer_telegram_callback,
    edit_telegram_message,
    fmt_duration,
    send_telegram_message,
)
from app.config import settings
from app.models import AlertChannel, Incident, Probe, ProbeResult, ProbeRollup, Server, Service
from app.models.monitoring import (
    STATUS_DEGRADED,
    STATUS_DOWN,
    STATUS_MAINTENANCE,
    STATUS_UNKNOWN,
    STATUS_UP,
)
from app.realtime import get_worker_heartbeat_age

logger = logging.getLogger("telegram_menu")

MAIN_MENU = {"inline_keyboard": [
    [{"text": "📊 Статус", "callback_data": "status"}, {"text": "⏱ Аптайм", "callback_data": "uptime"}],
    [{"text": "🩺 Health", "callback_data": "health"}, {"text": "❓ Помощь", "callback_data": "help"}],
]}
BACK_MENU = {"inline_keyboard": [[{"text": "‹ Назад", "callback_data": "menu"}]]}
UPTIME_MENU = {"inline_keyboard": [
    [{"text": "24ч", "callback_data": "uptime:24h"}, {"text": "7д", "callback_data": "uptime:7d"},
     {"text": "30д", "callback_data": "uptime:30d"}],
    [{"text": "‹ Назад", "callback_data": "menu"}],
]}

_PERIOD_DELTA = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
_PERIOD_ROLLUP = {"7d": "hour", "30d": "day"}  # 24h reads raw probe_results instead
_PERIOD_LABEL = {"24h": "24 часа", "7d": "7 дней", "30d": "30 дней"}


def _probe_query(db: Session, page_id: int | None):
    q = db.query(Probe).join(Server, Probe.server_id == Server.id).join(Service, Server.service_id == Service.id)
    if page_id is not None:
        q = q.filter(Service.page_id == page_id)
    return q


def build_status_report(db: Session, page_id: int | None) -> str:
    probes = _probe_query(db, page_id).filter(Probe.enabled.is_(True)).all()
    counts = {STATUS_UP: 0, STATUS_DEGRADED: 0, STATUS_DOWN: 0, STATUS_UNKNOWN: 0}
    for p in probes:
        counts[p.last_status] = counts.get(p.last_status, 0) + 1

    lines = [
        "📊 <b>Статус сейчас</b>",
        "",
        f"🟢 работает: {counts.get(STATUS_UP, 0)}   "
        f"🟠 деградация: {counts.get(STATUS_DEGRADED, 0)}   "
        f"🔴 недоступно: {counts.get(STATUS_DOWN, 0)}",
    ]

    inc_q = (
        db.query(Incident, Probe, Server)
        .join(Probe, Incident.probe_id == Probe.id)
        .join(Server, Probe.server_id == Server.id)
        .join(Service, Server.service_id == Service.id)
        .filter(Incident.resolved_at.is_(None))
    )
    if page_id is not None:
        inc_q = inc_q.filter(Service.page_id == page_id)
    open_incidents = inc_q.order_by(Incident.started_at).all()

    lines.append("")
    if open_incidents:
        now = datetime.now(timezone.utc)
        lines.append(f"<b>Открытые инциденты ({len(open_incidents)}):</b>")
        for inc, probe, server in open_incidents[:15]:
            elapsed = fmt_duration((now - inc.started_at).total_seconds())
            lines.append(f"• {server.name} · {probe.name} — {elapsed}")
        if len(open_incidents) > 15:
            lines.append(f"…и ещё {len(open_incidents) - 15}")
    else:
        lines.append("Открытых инцидентов нет ✅")

    return "\n".join(lines)


def build_uptime_report(db: Session, page_id: int | None, period: str) -> str:
    delta = _PERIOD_DELTA.get(period, _PERIOD_DELTA["24h"])
    since = datetime.now(timezone.utc) - delta
    probe_ids = [pid for (pid,) in _probe_query(db, page_id).filter(Probe.enabled.is_(True))
                 .with_entities(Probe.id).all()]
    label = _PERIOD_LABEL.get(period, period)
    if not probe_ids:
        return f"⏱ <b>Аптайм за {label}</b>\n\nНет активных проб в этой области."

    rollup_period = _PERIOD_ROLLUP.get(period)
    if rollup_period:
        rows = db.query(ProbeRollup).filter(
            ProbeRollup.probe_id.in_(probe_ids), ProbeRollup.period == rollup_period,
            ProbeRollup.bucket >= since,
        ).all()
        total = sum(r.total for r in rows)
        up = sum(r.up_count for r in rows)
    else:
        results = db.query(ProbeResult).filter(
            ProbeResult.probe_id.in_(probe_ids), ProbeResult.checked_at >= since,
        ).all()
        counted = [r for r in results if r.status not in (STATUS_MAINTENANCE, STATUS_UNKNOWN)]
        total = len(counted)
        up = sum(1 for r in counted if r.status == STATUS_UP)

    pct = (up / total * 100) if total else 100.0
    return f"⏱ <b>Аптайм за {label}</b>\n\n{pct:.2f}% ({up}/{total} успешных проверок)"


def build_health_report(db: Session) -> str:
    age = get_worker_heartbeat_age()
    if age is None:
        worker_line = "❓ нет данных о воркере"
    elif age <= settings.worker_stale_sec:
        worker_line = f"🟢 воркер жив (heartbeat {age:.0f}с назад)"
    else:
        worker_line = f"🔴 воркер не отвечает (heartbeat {age:.0f}с назад, порог {settings.worker_stale_sec}с)"

    now = datetime.now(timezone.utc)
    probes = db.query(Probe).filter(Probe.enabled.is_(True)).all()
    overdue = [
        p for p in probes
        if p.last_checked_at is None or (now - p.last_checked_at).total_seconds() > p.interval_sec * 3
    ]

    lines = ["🩺 <b>Health мониторинга</b>", "", worker_line, f"Зависших проб: {len(overdue)}"]
    if overdue:
        lines.append("")
        for p in overdue[:10]:
            lines.append(f"• {p.name}")
        if len(overdue) > 10:
            lines.append(f"…и ещё {len(overdue) - 10}")
    return "\n".join(lines)


def build_help_report() -> str:
    return (
        "❓ <b>Команды</b>\n\n"
        "/menu — открыть меню с кнопками\n"
        "/status — статус сейчас\n"
        "/uptime — аптайм за период\n"
        "/health — здоровье мониторинга"
    )


def render_screen(db: Session, page_id: int | None, key: str) -> tuple[str, dict]:
    """(text, inline_keyboard) for a given menu key. Unknown keys fall back to
    the main menu rather than erroring, since Telegram may replay stale
    callback_data after a restart or config change."""
    if key == "status":
        return build_status_report(db, page_id), BACK_MENU
    if key == "uptime":
        return "⏱ <b>Аптайм</b>\n\nЗа какой период?", UPTIME_MENU
    if key and key.startswith("uptime:"):
        return build_uptime_report(db, page_id, key.split(":", 1)[1]), UPTIME_MENU
    if key == "health":
        return build_health_report(db), BACK_MENU
    if key == "help":
        return build_help_report(), BACK_MENU
    return "📋 <b>Меню</b>\n\nВыбери, что показать:", MAIN_MENU


_COMMANDS = {"/menu": "menu", "/start": "menu", "/status": "status",
             "/uptime": "uptime", "/health": "health", "/help": "help"}


def handle_update(db: Session, channel: AlertChannel, update: dict) -> None:
    """Route one Telegram update (message or button tap) for this channel.
    Ignores anything not from the channel's own chat_id — the webhook path
    already scopes to one channel, this is a second, cheap safety check."""
    cfg = channel.config or {}
    chat_id = str(cfg.get("chat_id") or "")

    callback = update.get("callback_query")
    if callback is not None:
        msg = callback.get("message") or {}
        if str((msg.get("chat") or {}).get("id", "")) != chat_id:
            return
        answer_telegram_callback(cfg, callback["id"])
        text, keyboard = render_screen(db, channel.page_id, callback.get("data") or "menu")
        try:
            edit_telegram_message(cfg, msg["message_id"], text, reply_markup=keyboard)
        except Exception as exc:  # noqa: BLE001 — best-effort, e.g. message too old to edit
            logger.warning("telegram menu: edit failed (channel %s): %s", channel.id, exc)
        return

    message = update.get("message")
    if message is None:
        return
    if str((message.get("chat") or {}).get("id", "")) != chat_id:
        return
    text = (message.get("text") or "").strip().split()[:1]
    command = text[0].split("@")[0] if text else ""  # strip /cmd@BotName group-chat suffix
    key = _COMMANDS.get(command)
    if key is None:
        return
    body, keyboard = render_screen(db, channel.page_id, key)
    try:
        send_telegram_message(cfg, body, parse_mode="HTML", reply_markup=keyboard)
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram menu: reply failed (channel %s): %s", channel.id, exc)
