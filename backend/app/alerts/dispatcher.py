"""Alert dispatch.

- Channels are split into "base" (escalate_after_min == 0, alert immediately) and
  "escalation" (escalate_after_min > 0, only after an incident has stayed open).
- Per-page routing: a channel with a page_id only receives that page's alerts.
- Storm grouping: "down"/"resolved" alerts for the same server are coalesced
  within a short window so a whole-server outage sends one alert, not one per probe.
"""

import logging
import smtplib
import threading
import time
from email.message import EmailMessage

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.alert import AlertChannel

logger = logging.getLogger("alerts")

_TIMEOUT = 10

_VERB = {
    "opened": "🔴 УПАЛ",
    "ongoing": "🔴 ВСЁ ЕЩЁ ЛЕЖИТ",
    "escalated": "🔺 ЭСКАЛАЦИЯ",
    "resolved": "🟢 ВОССТАНОВЛЕН",
}

# Event types a channel may subscribe to via config["events"].
ALERT_EVENTS = tuple(_VERB.keys())

# Placeholders available in a channel's custom config["template"].
TEMPLATE_FIELDS = ("event", "verb", "probe", "host", "status", "error")


class _SafeDict(dict):
    """format_map helper: leave unknown placeholders literal instead of raising."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _render_template(template: str | None, fields: dict, default: str) -> str:
    """Render a channel's custom message template, falling back to ``default``
    when no template is set or rendering fails (e.g. malformed braces)."""
    if not template:
        return default
    try:
        return template.format_map(_SafeDict(fields))
    except Exception as exc:  # noqa: BLE001
        logger.warning("alert template render failed: %s", exc)
        return default

# in-memory storm-grouping gate (worker is single-process)
_gate_lock = threading.Lock()
_last_group_alert: dict[tuple[int, str], float] = {}


def _group_allows(server_id: int | None, event: str) -> bool:
    """True if an alert for (server, event-class) may be sent now (storm grouping)."""
    window = settings.alert_group_window_sec
    if window <= 0 or server_id is None:
        return True
    # group "ongoing"/"escalated" with their base class
    cls = "down" if event in ("opened", "ongoing", "escalated") else "resolved"
    key = (server_id, cls)
    now = time.time()
    with _gate_lock:
        last = _last_group_alert.get(key)
        if last is not None and now - last < window:
            return False
        _last_group_alert[key] = now
        return True


def _deliver(fn) -> None:
    """Call a best-effort send function, retrying transient failures with
    exponential backoff. Re-raises the last error if every attempt fails so the
    caller can log it."""
    attempts = max(1, settings.alert_retry_attempts)
    backoff = settings.alert_retry_backoff_sec
    last: Exception | None = None
    for i in range(attempts):
        try:
            fn()
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            if i + 1 < attempts:
                time.sleep(backoff * (2 ** i))
    if last is not None:
        raise last


def _send_telegram(config: dict, text: str) -> None:
    token, chat_id = config.get("bot_token"), config.get("chat_id")
    if not token or not chat_id:
        logger.warning("telegram channel misconfigured")
        return
    proxy = config.get("proxy") or None
    with httpx.Client(timeout=_TIMEOUT, proxy=proxy) as client:
        resp = client.post(f"https://api.telegram.org/bot{token}/sendMessage",
                           json={"chat_id": chat_id, "text": text})
        resp.raise_for_status()


def test_telegram(token: str, proxy: str | None) -> tuple[bool, str]:
    """Validate a Telegram bot token and reachability (optionally via a SOCKS5
    proxy) by calling getMe. Returns (ok, detail)."""
    if not token:
        return False, "bot_token required"
    try:
        with httpx.Client(timeout=_TIMEOUT, proxy=proxy or None) as client:
            resp = client.get(f"https://api.telegram.org/bot{token}/getMe")
        data = resp.json()
        if resp.status_code == 200 and data.get("ok"):
            return True, "@" + data["result"].get("username", "bot")
        return False, data.get("description", f"HTTP {resp.status_code}")
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _send_webhook(config: dict, payload: dict) -> None:
    url = config.get("url")
    if not url:
        logger.warning("webhook channel misconfigured")
        return
    headers = {}
    if config.get("secret_header") and config.get("secret_value"):
        headers[config["secret_header"]] = config["secret_value"]
    resp = httpx.post(url, json=payload, headers=headers, timeout=_TIMEOUT)
    resp.raise_for_status()


def _send_email(config: dict, subject: str, text: str) -> None:
    host, to_addr = config.get("smtp_host"), config.get("to")
    from_addr = config.get("from") or config.get("username")
    if not host or not to_addr or not from_addr:
        logger.warning("email channel misconfigured")
        return
    port = int(config.get("smtp_port", 587))
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, from_addr, to_addr
    msg.set_content(text)
    use_ssl = bool(config.get("use_ssl", port == 465))
    smtp = smtplib.SMTP_SSL(host, port, timeout=_TIMEOUT) if use_ssl else smtplib.SMTP(host, port, timeout=_TIMEOUT)
    try:
        if not use_ssl and config.get("use_tls", True):
            smtp.starttls()
        if config.get("username") and config.get("password"):
            smtp.login(config["username"], config["password"])
        smtp.send_message(msg)
    finally:
        smtp.quit()


def base_channels(db: Session, page_id: int | None) -> list[AlertChannel]:
    return _channels(db, page_id).filter(AlertChannel.escalate_after_min == 0).all()


def escalation_channels(db: Session, page_id: int | None, max_min: int) -> list[AlertChannel]:
    return (
        _channels(db, page_id)
        .filter(AlertChannel.escalate_after_min > 0, AlertChannel.escalate_after_min <= max_min)
        .all()
    )


def all_escalation_channels(db: Session, page_id: int | None) -> list[AlertChannel]:
    return _channels(db, page_id).filter(AlertChannel.escalate_after_min > 0).all()


def _channels(db: Session, page_id: int | None):
    return db.query(AlertChannel).filter(
        AlertChannel.enabled.is_(True),
        (AlertChannel.page_id.is_(None)) | (AlertChannel.page_id == page_id),
    )


def dispatch(channels: list[AlertChannel], *, event: str, probe_name: str, server_host: str,
             status: str, error: str | None, server_id: int | None = None, group: bool = True) -> None:
    """Send an alert to the given channels (best-effort, storm-grouped)."""
    if not channels:
        return
    if group and not _group_allows(server_id, event):
        logger.info("alert grouped/suppressed: server=%s event=%s", server_id, event)
        return

    verb = _VERB.get(event, event.upper())
    default_text = f"{verb}: {probe_name} на {server_host}\nстатус: {status}" + (f"\nошибка: {error}" if error else "")
    fields = {"event": event, "verb": verb, "probe": probe_name,
              "host": server_host, "status": status, "error": error or ""}

    for ch in channels:
        cfg = ch.config or {}
        # Per-channel event subscription: empty/absent list = receive every event.
        subscribed = cfg.get("events")
        if subscribed and event not in subscribed:
            continue
        text = _render_template(cfg.get("template"), fields, default_text)
        payload = {"event": event, "probe": probe_name, "host": server_host,
                   "status": status, "error": error, "text": text}
        try:
            if ch.type == "telegram":
                _deliver(lambda cfg=cfg, text=text: _send_telegram(cfg, text))
            elif ch.type == "webhook":
                _deliver(lambda cfg=cfg, payload=payload: _send_webhook(cfg, payload))
            elif ch.type == "email":
                _deliver(lambda cfg=cfg, text=text: _send_email(cfg, subject=f"{verb}: {probe_name}", text=text))
        except Exception as exc:  # noqa: BLE001
            logger.warning("alert channel %s (%s) failed after %s attempt(s): %s",
                           ch.id, ch.type, max(1, settings.alert_retry_attempts), exc)
