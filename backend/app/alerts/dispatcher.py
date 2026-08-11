"""Alert dispatch.

- Channels are split into "base" (escalate_after_min == 0, alert immediately) and
  "escalation" (escalate_after_min > 0, only after an incident has stayed open).
- Per-page routing: a channel with a page_id only receives that page's alerts.
- Storm grouping: "down"/"resolved" alerts for the same server are coalesced
  within a short window so a whole-server outage sends one alert, not one per probe.
"""

import html
import json
import logging
import smtplib
import threading
import time
from datetime import datetime, timezone
from email.message import EmailMessage

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models.alert import AlertChannel
from app.realtime import get_sync_redis

logger = logging.getLogger("alerts")

_TIMEOUT = 10

_VERB = {
    "opened": "🔴 УПАЛ",
    "ongoing": "🔴 ВСЁ ЕЩЁ ЛЕЖИТ",
    "escalated": "🔺 ЭСКАЛАЦИЯ",
    "resolved": "🟢 ВОССТАНОВЛЕН",
    "ip_changed": "🔄 IP ИЗМЕНИЛСЯ",
    "cert_changed": "📜 СЕРТИФИКАТ ИЗМЕНИЛСЯ",
    "content_changed": "📝 КОНТЕНТ ИЗМЕНИЛСЯ",
    "cert_expiring": "⏳ СЕРТИФИКАТ ИСТЕКАЕТ",
}

# Event types a channel may subscribe to via config["events"].
ALERT_EVENTS = tuple(_VERB.keys())

# Placeholders available in a channel's custom config["template"].
TEMPLATE_FIELDS = (
    "event", "verb", "probe", "type", "host", "server", "service", "page",
    "status", "error", "latency", "duration", "url", "time", "alert_count",
)

_STATUS_RU = {"up": "работает", "degraded": "деградация", "down": "недоступен",
              "unknown": "неизвестно", "paused": "на паузе"}


def _header(event: str, status: str) -> tuple[str, str]:
    """(emoji, plain title) for an event, severity-aware for 'opened'."""
    if event == "opened":
        return ("🟠", "Деградация") if status == "degraded" else ("🔴", "Сбой")
    return {
        "ongoing": ("🔴", "Всё ещё недоступен"),
        "escalated": ("🔺", "Эскалация"),
        "resolved": ("🟢", "Восстановлен"),
        "ip_changed": ("🔄", "IP-адрес изменился"),
        "cert_changed": ("📜", "SSL-сертификат изменился"),
        "content_changed": ("📝", "Содержимое страницы изменилось"),
        "cert_expiring": ("⏳", "Сертификат скоро истекает"),
    }.get(event, ("⚪", event))


def _fmt_duration(seconds: float) -> str:
    s = int(max(0, seconds))
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m, sec = divmod(rem, 60)
    parts = []
    if d:
        parts.append(f"{d} д")
    if h:
        parts.append(f"{h} ч")
    if m:
        parts.append(f"{m} мин")
    if sec or not parts:
        parts.append(f"{sec} с")
    return " ".join(parts)


def _build_messages(ctx: dict) -> tuple[str, str, str, list[tuple[str, str, str]]]:
    """Build (plain, html, head_plain, rows) for an alert. HTML uses
    Telegram-supported tags (<b>, <code>, <a>, <blockquote expandable>); plain
    mirrors it for email/webhook and template fallback. head_plain/rows are
    exposed separately so the Telegram "table" style can reuse the same fields
    inside a Rich Message table block instead of the plain-text list."""
    emoji, title = _header(ctx["event"], ctx["status"])
    status_ru = _STATUS_RU.get(ctx["status"], ctx["status"])

    def esc(v) -> str:
        return html.escape(str(v)) if v is not None else ""

    # Server name + IP move into the headline so "что случилось / какой сервер"
    # is visible without opening the collapsed details block below.
    srv = ctx.get("server") or ""
    server_plain = f"{srv} · {ctx['host']}".strip(" ·")
    server_html = (esc(srv) + " · " if srv else "") + f"<code>{esc(ctx['host'])}</code>"

    rows: list[tuple[str, str, str]] = []
    rows.append(("Проба", ctx["probe"] + (f" · {ctx['type'].upper()}" if ctx.get("type") else ""),
                 esc(ctx["probe"]) + (f" · {esc(ctx['type'].upper())}" if ctx.get("type") else "")))
    loc = " · ".join(x for x in (ctx.get("service"), ctx.get("page")) if x)
    if loc:
        rows.append(("Где", loc, esc(loc)))
    if ctx.get("latency") is not None:
        lat = f"{ctx['latency']:.0f} мс"
        rows.append(("Время ответа", lat, esc(lat)))
    if ctx.get("error"):
        err_label = {
            "ip_changed": "IP", "cert_changed": "Сертификат",
            "content_changed": "Контент", "cert_expiring": "Сертификат",
        }.get(ctx["event"], "Ошибка")
        rows.append((err_label, ctx["error"], f"<code>{esc(ctx['error'])}</code>"))
    if ctx.get("duration"):
        label = "Простой" if ctx["event"] == "resolved" else "Длится уже"
        rows.append((label, ctx["duration"], esc(ctx["duration"])))
    if ctx["event"] == "resolved" and ctx.get("alert_count"):
        n = str(ctx["alert_count"])
        rows.append(("Алертов по инциденту", n, esc(n)))
    rows.append(("Время", ctx["time"], esc(ctx["time"])))

    # Append the current status only when it isn't already implied by the title
    # (avoids "Деградация · деградация" / "Всё ещё недоступен · недоступен"). The
    # IP-/cert-change alerts are about the address/certificate, not the up/down
    # state, so they carry no status suffix.
    _no_suffix = ("ip_changed", "cert_changed", "content_changed", "cert_expiring")
    suffix = "" if (ctx["event"] in _no_suffix or status_ru.lower() in title.lower()) else f" · {status_ru}"
    head_plain = f"{emoji} {title.upper()}{suffix.upper()} — {server_plain}"
    head_html = f"{emoji} <b>{esc(title)}</b>{esc(suffix)} — {server_html}"
    body_plain = "\n".join(f"{label}: {pv}" for label, pv, _ in rows)
    body_html = "\n".join(f"<b>{esc(label)}:</b> {hv}" for label, _, hv in rows)

    link_plain = link_html = ""
    if ctx.get("url"):
        link_plain = f"\n\n🔗 {ctx['url']}"
        link_html = f'\n\n🔗 <a href="{esc(ctx["url"])}">Открыть статус-страницу</a>'

    return (f"{head_plain}\n\n{body_plain}{link_plain}",
            f"{head_html}\n\n<blockquote expandable>{body_html}</blockquote>{link_html}",
            head_plain, rows)


# Telegram-only: how a channel renders its message. "default" is the classic
# HTML text above; "table" sends the fields as a Rich Message table via the
# Bot API 10.1+ sendRichMessage method (see _table_blocks/_send_telegram_rich).
TELEGRAM_MESSAGE_STYLES = ("default", "table")


def _table_blocks(head_plain: str, rows: list[tuple[str, str, str]], url: str | None) -> list[dict]:
    """Rich Message blocks for a Telegram sendRichMessage table alert. Cell
    shape (flat "text" leaves in a 2D "cells" array) confirmed working against
    the live Bot API — see git history for the schema variants that 400'd or
    rendered empty before this one."""
    def _cell(text: str, header: bool = False) -> dict:
        cell: dict = {"text": text}
        if header:
            cell["header"] = True
        return cell

    blocks: list[dict] = [{"type": "paragraph", "text": head_plain}]
    if rows:
        table_cells: list[list[dict]] = [
            [_cell("Поле", header=True), _cell("Значение", header=True)],
        ]
        for label, value, _ in rows:
            table_cells.append([_cell(label), _cell(value)])
        blocks.append({
            "type": "table",
            "bordered": True,
            "striped": True,
            "cells": table_cells,
        })
    if url:
        blocks.append({"type": "paragraph", "text": f"🔗 {url}"})
    return blocks


def _table_preview_html(head_plain: str, rows: list[tuple[str, str, str]], url: str | None) -> str:
    """Browser-renderable approximation of the table alert for the admin
    preview panel — NOT what's actually sent (real delivery goes through
    sendRichMessage; see _table_blocks/_send_telegram_rich)."""
    def esc(v) -> str:
        return html.escape(str(v))

    table_rows = "".join(
        f"<tr><td>{esc(label)}</td><td>{esc(value)}</td></tr>" for label, value, _ in rows
    )
    link = f'<div>🔗 <a href="{esc(url)}">Открыть статус-страницу</a></div>' if url else ""
    return (
        f"<div>{esc(head_plain)}</div>"
        f'<table class="alert-preview-table"><tr><th>Поле</th><th>Значение</th></tr>{table_rows}</table>'
        f"{link}"
    )


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
    """True if an alert for (server, event-class) may be sent now (storm grouping).

    Backed by Redis (SET NX EX) so grouping works across multiple worker
    processes; falls back to an in-memory gate if Redis is unavailable."""
    window = settings.alert_group_window_sec
    if window <= 0 or server_id is None:
        return True
    # group "ongoing"/"escalated" with their base class
    cls = "down" if event in ("opened", "ongoing", "escalated") else "resolved"
    try:
        # Acquire a per-(server, class) gate that auto-expires after the window.
        # set(nx=True) returns True only for the first caller within the window.
        acquired = get_sync_redis().set(f"alert:grp:{server_id}:{cls}", "1", nx=True, ex=window)
        return bool(acquired)
    except Exception as exc:  # noqa: BLE001 — Redis down: degrade to in-memory
        logger.warning("storm-group Redis gate failed, using in-memory: %s", exc)
        now = time.time()
        with _gate_lock:
            last = _last_group_alert.get((server_id, cls))
            if last is not None and now - last < window:
                return False
            _last_group_alert[(server_id, cls)] = now
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


def _send_telegram(config: dict, text: str, parse_mode: str | None = None) -> None:
    token, chat_id = config.get("bot_token"), config.get("chat_id")
    if not token or not chat_id:
        logger.warning("telegram channel misconfigured")
        return
    proxy = config.get("proxy") or None
    body: dict = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if parse_mode:
        body["parse_mode"] = parse_mode
    with httpx.Client(timeout=_TIMEOUT, proxy=proxy) as client:
        resp = client.post(f"https://api.telegram.org/bot{token}/sendMessage", json=body)
        resp.raise_for_status()


def _send_telegram_rich(config: dict, blocks: list[dict]) -> None:
    """Send a Telegram Rich Message (Bot API 10.1+ sendRichMessage). Schema is
    best-effort (see _table_blocks) — raises on any non-2xx so the caller can
    fall back to the classic HTML message."""
    token, chat_id = config.get("bot_token"), config.get("chat_id")
    if not token or not chat_id:
        logger.warning("telegram channel misconfigured")
        return
    proxy = config.get("proxy") or None
    body = {"chat_id": chat_id, "rich_message": {"blocks": blocks}}
    with httpx.Client(timeout=_TIMEOUT, proxy=proxy) as client:
        resp = client.post(f"https://api.telegram.org/bot{token}/sendRichMessage", json=body)
        if resp.status_code >= 400:
            # Telegram puts the real reason in the JSON body ("description"), not
            # in the HTTP status line — surface it so failures are diagnosable
            # instead of a bare "400 Bad Request".
            try:
                detail = resp.json().get("description") or resp.text
            except Exception:  # noqa: BLE001
                detail = resp.text
            raise RuntimeError(f"sendRichMessage HTTP {resp.status_code}: {detail}")


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


def list_telegram_chats(token: str | None, proxy: str | None) -> tuple[bool, list[dict] | str]:
    """Discover chats the bot can see via getUpdates, so the operator can pick a
    chat_id instead of hunting for it. Returns (ok, chats) or (False, error).
    A chat appears only after someone has messaged the bot / added it to a group."""
    if not token:
        return False, "bot_token required"
    try:
        with httpx.Client(timeout=_TIMEOUT, proxy=proxy or None) as client:
            resp = client.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params={"limit": 100, "timeout": 0},
            )
        data = resp.json()
        if resp.status_code != 200 or not data.get("ok"):
            return False, data.get("description", f"HTTP {resp.status_code}")
        chats: dict = {}
        for upd in data.get("result", []):
            for key in ("message", "edited_message", "channel_post", "my_chat_member"):
                obj = upd.get(key)
                chat = obj.get("chat") if isinstance(obj, dict) else None
                if not chat:
                    continue
                title = (
                    chat.get("title")
                    or " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x)
                    or (f"@{chat['username']}" if chat.get("username") else "")
                    or str(chat["id"])
                )
                chats[chat["id"]] = {"id": chat["id"], "title": title, "type": chat.get("type", "")}
        return True, list(chats.values())
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _sample_ctx() -> dict:
    """A representative alert context used by the end-to-end channel tests."""
    now = datetime.now(timezone.utc)
    return {
        "event": "opened", "probe": "Тестовая проба", "type": "https",
        "host": "example.com", "server": "Тест-сервер", "service": "Проверка",
        "page": "", "status": "down", "error": "Это тестовый алерт",
        "latency": 123.0, "duration": _fmt_duration(90),
        "url": "", "time": now.strftime("%d.%m.%Y %H:%M:%S UTC"),
    }


def render_preview(channel_type: str, config: dict) -> dict:
    """Render exactly what a channel would send for a representative incident, so
    the operator can preview a custom template (or the default message) live.
    Returns {"text": str, "is_html": bool} — is_html marks Telegram's HTML body."""
    ctx = _sample_ctx()
    verb_emoji, verb_title = _header(ctx["event"], ctx["status"])
    verb = f"{verb_emoji} {verb_title}"
    default_plain, default_html, head_plain, rows = _build_messages(ctx)
    fields = {**ctx, "verb": verb,
              "latency": f"{ctx['latency']:.0f}" if ctx.get("latency") is not None else ""}
    template = config.get("template")
    if not template:
        # Telegram sends the rich HTML body (or a Rich Message table); webhook/email
        # get the plain mirror.
        if channel_type == "telegram":
            if config.get("message_style") == "table":
                return {"text": _table_preview_html(head_plain, rows, ctx.get("url")), "is_html": True}
            return {"text": default_html, "is_html": True}
        return {"text": default_plain, "is_html": False}
    # A custom template is always delivered as plain text (no parse_mode).
    return {"text": _render_template(template, fields, default_plain), "is_html": False}


def send_test_telegram(config: dict) -> tuple[bool, str]:
    """Deliver a sample formatted alert to the channel's Telegram chat so the
    operator can confirm the bot token, chat_id (and proxy) actually work
    end-to-end. Returns (ok, detail)."""
    if not config.get("bot_token"):
        return False, "bot_token required"
    if not config.get("chat_id"):
        return False, "chat_id required"
    _, default_html, head_plain, rows = _build_messages(_sample_ctx())
    if config.get("message_style") == "table":
        try:
            _send_telegram_rich(config, _table_blocks("🧪 ТЕСТ\n" + head_plain, rows, None))
            return True, "Тестовая Rich Message-таблица отправлена в Telegram"
        except Exception as exc:  # noqa: BLE001
            return False, f"sendRichMessage не сработал ({exc}); в реальных алертах сработает откат на обычный HTML"
    text = "🧪 <b>ТЕСТ</b>\n\n" + default_html
    try:
        _send_telegram(config, text, parse_mode="HTML")
        return True, "Тестовый алерт отправлен в Telegram"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


WEBHOOK_FORMATS = ("generic", "slack", "discord", "mattermost")


def _webhook_body(config: dict, payload: dict) -> dict:
    """Shape the outgoing JSON for the target service so it renders as a message:
    Slack/Mattermost incoming webhooks expect {"text": …}, Discord {"content": …};
    'generic' (default) posts the full structured payload."""
    fmt = config.get("format") or "generic"
    text = payload.get("text", "")
    if fmt in ("slack", "mattermost"):
        return {"text": text}
    if fmt == "discord":
        return {"content": text}
    return payload


def _send_webhook(config: dict, payload: dict) -> None:
    url = config.get("url")
    if not url:
        logger.warning("webhook channel misconfigured")
        return
    headers = {}
    if config.get("secret_header") and config.get("secret_value"):
        headers[config["secret_header"]] = config["secret_value"]
    resp = httpx.post(url, json=_webhook_body(config, payload), headers=headers, timeout=_TIMEOUT)
    resp.raise_for_status()


def test_webhook(config: dict) -> tuple[bool, str]:
    """POST a tiny ping payload to the webhook URL and check for a 2xx. Returns
    (ok, detail)."""
    if not config.get("url"):
        return False, "url required"
    try:
        _send_webhook(config, {"event": "test", "text": "🧪 monitoring webhook test"})
        return True, "Вебхук принял запрос (2xx)"
    except httpx.HTTPStatusError as exc:
        return False, f"HTTP {exc.response.status_code}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def send_test_webhook(config: dict) -> tuple[bool, str]:
    """POST a full sample alert payload to the webhook so the operator can verify
    the receiving side parses it end-to-end. Returns (ok, detail)."""
    if not config.get("url"):
        return False, "url required"
    ctx = _sample_ctx()
    plain, _ = _build_messages(ctx)
    payload = {
        "event": ctx["event"], "probe": ctx["probe"], "type": ctx["type"],
        "host": ctx["host"], "server": ctx["server"], "service": ctx["service"],
        "page": ctx["page"], "status": ctx["status"], "error": ctx["error"],
        "latency_ms": ctx["latency"], "duration": ctx["duration"], "url": ctx["url"],
        "test": True, "text": "🧪 ТЕСТ\n\n" + plain,
    }
    try:
        _send_webhook(config, payload)
        return True, "Тестовый вебхук отправлен"
    except httpx.HTTPStatusError as exc:
        return False, f"HTTP {exc.response.status_code}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _smtp_connect(config: dict):
    """Open an SMTP connection per the channel config (SSL/STARTTLS + optional
    login). The caller must quit() it."""
    host = config["smtp_host"]
    port = int(config.get("smtp_port", 587))
    use_ssl = bool(config.get("use_ssl", port == 465))
    smtp = (smtplib.SMTP_SSL(host, port, timeout=_TIMEOUT) if use_ssl
            else smtplib.SMTP(host, port, timeout=_TIMEOUT))
    if not use_ssl and config.get("use_tls", True):
        smtp.starttls()
    if config.get("username") and config.get("password"):
        smtp.login(config["username"], config["password"])
    return smtp


def test_email(config: dict) -> tuple[bool, str]:
    """Open an SMTP session (TLS + optional auth) and NOOP, without sending mail,
    to confirm the host/port/credentials work. Returns (ok, detail)."""
    if not config.get("smtp_host"):
        return False, "smtp_host required"
    try:
        smtp = _smtp_connect(config)
        try:
            smtp.noop()
        finally:
            smtp.quit()
        return True, f"SMTP {config['smtp_host']}:{config.get('smtp_port', 587)} OK"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def send_test_email(config: dict) -> tuple[bool, str]:
    """Send a sample alert email so the operator can confirm delivery end-to-end.
    Returns (ok, detail)."""
    if not config.get("smtp_host"):
        return False, "smtp_host required"
    if not config.get("to"):
        return False, "to required"
    if not (config.get("from") or config.get("username")):
        return False, "from required"
    plain, _ = _build_messages(_sample_ctx())
    try:
        _send_email(config, subject="🧪 ТЕСТ: мониторинг", text="🧪 ТЕСТ\n\n" + plain)
        return True, "Тестовое письмо отправлено"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _send_email(config: dict, subject: str, text: str) -> None:
    host, to_addr = config.get("smtp_host"), config.get("to")
    from_addr = config.get("from") or config.get("username")
    if not host or not to_addr or not from_addr:
        logger.warning("email channel misconfigured")
        return
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, from_addr, to_addr
    msg.set_content(text)
    smtp = _smtp_connect(config)
    try:
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


def record_deliveries(outcomes: list[tuple[int, bool, str | None]]) -> None:
    """Persist the last-delivery status for each channel id. Uses its own session
    so it never commits the caller's in-flight transaction. Best-effort: a failure
    here must never break alert dispatch."""
    if not outcomes:
        return
    sent_at = datetime.now(timezone.utc)
    try:
        with SessionLocal() as session:
            for channel_id, ok, err in outcomes:
                ch = session.get(AlertChannel, channel_id)
                if ch is None:
                    continue
                ch.last_sent_at = sent_at
                ch.last_ok = ok
                ch.last_error = None if ok else (err or "")[:500]
            session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("recording alert delivery status failed: %s", exc)


# --- Storm grouping: fold multiple servers going down together into one alert.
# The first "opened" alert for a service is buffered instead of sent, starting a
# short window (ZSET member=service_id, score=due-at unix ts, NX so later
# arrivals don't reset the clock). Siblings that go down within the window join
# the same buffer (a Redis list of JSON records). flush_storm_alerts(), run
# periodically by the worker, sends whatever accumulated once the window elapses
# — one server -> a normal single alert (just delayed), several -> one combined
# message. This trades a few seconds of alert latency for not spamming the chat
# with one message per server during a whole-service outage.
_STORM_QUEUE_KEY = "alert:storm:queue"


def _storm_items_key(service_id: int) -> str:
    return f"alert:storm:items:{service_id}"


def queue_storm_alert(*, service_id: int, page_id: int | None, window_sec: int, record: dict) -> None:
    """Buffer an "opened" alert for storm-window grouping. Raises on Redis
    failure — the caller should catch and send immediately as a fallback."""
    r = get_sync_redis()
    payload = dict(record)
    payload["page_id"] = page_id
    for key in ("occurred_at", "started_at"):
        v = payload.get(key)
        if isinstance(v, datetime):
            payload[key] = v.isoformat()
    r.zadd(_STORM_QUEUE_KEY, {str(service_id): time.time() + window_sec}, nx=True)
    r.rpush(_storm_items_key(service_id), json.dumps(payload))
    r.expire(_storm_items_key(service_id), window_sec + 30)


def flush_storm_alerts() -> None:
    """Send combined alerts for any storm-grouping windows that have elapsed.
    Safe to call from overlapping periodic ticks — zrem() only lets one call
    claim a given service's group, so a group is never sent twice."""
    try:
        r = get_sync_redis()
        due = r.zrangebyscore(_STORM_QUEUE_KEY, 0, time.time())
    except Exception as exc:  # noqa: BLE001
        logger.warning("storm flush: redis unavailable: %s", exc)
        return
    for member in due:
        try:
            if not r.zrem(_STORM_QUEUE_KEY, member):
                continue  # another tick already claimed this group
            items_key = _storm_items_key(int(member))
            raw = r.lrange(items_key, 0, -1)
            r.delete(items_key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("storm flush: reading group %s failed: %s", member, exc)
            continue
        if not raw:
            continue
        try:
            records = [json.loads(x) for x in raw]
        except Exception as exc:  # noqa: BLE001
            logger.warning("storm flush: bad JSON in group %s: %s", member, exc)
            continue
        _send_storm_group(records)


def _send_storm_group(records: list[dict]) -> None:
    """A single buffered record -> a normal delayed 'opened' alert; several ->
    one combined message. Runs from a periodic tick, not the original request,
    so it opens its own DB session."""
    page_id = records[0].get("page_id")
    with SessionLocal() as db:
        channels = base_channels(db, page_id)
        if not channels:
            return
        if len(records) == 1:
            rec = dict(records[0])
            rec.pop("page_id", None)
            started_raw = rec.pop("started_at", None)
            occurred_raw = rec.pop("occurred_at", None)
            dispatch(
                channels, event="opened",
                started_at=datetime.fromisoformat(started_raw) if started_raw else None,
                occurred_at=datetime.fromisoformat(occurred_raw) if occurred_raw else None,
                **rec,
            )
        else:
            occurred_raw = records[0].get("occurred_at")
            occurred_at = datetime.fromisoformat(occurred_raw) if occurred_raw else datetime.now(timezone.utc)
            dispatch_group(
                channels, records=records, occurred_at=occurred_at,
                service_name=records[0].get("service_name", ""),
                page_title=records[0].get("page_title", ""),
                page_url=records[0].get("page_url", ""),
            )


def dispatch_group(channels: list[AlertChannel], *, records: list[dict], occurred_at: datetime,
                    service_name: str, page_title: str, page_url: str) -> None:
    """Send one combined "opened" alert for multiple servers in the same
    service that went down within the storm-grouping window. Simpler than
    dispatch(): no per-channel templates/table style, no per-server gating (the
    storm window already served that purpose)."""
    if not channels:
        return

    def esc(v) -> str:
        return html.escape(str(v)) if v is not None else ""

    loc = " · ".join(x for x in (service_name, page_title) if x)
    time_str = occurred_at.strftime("%d.%m.%Y %H:%M:%S UTC")
    probes = sorted({r.get("probe_name", "") for r in records if r.get("probe_name")})
    probe_line = ", ".join(probes)

    lines_plain = [
        f"{r.get('server_name', '')} {r.get('server_host', '')} — {r.get('error') or r.get('status', '')}"
        for r in records
    ]
    lines_html = [
        f"{esc(r.get('server_name', ''))} · <code>{esc(r.get('server_host', ''))}</code> — "
        f"<code>{esc(r.get('error') or r.get('status', ''))}</code>"
        for r in records
    ]

    head_plain = f"🟠 ДЕГРАДАЦИЯ · {len(records)} СЕРВЕРОВ"
    head_html = f"🟠 <b>Деградация · {len(records)} серверов</b>"
    body_plain = "\n".join(
        ([loc] if loc else []) + [f"• {l}" for l in lines_plain] + [f"Проба: {probe_line}", f"Время: {time_str}"]
    )
    body_html = "\n".join(
        ([esc(loc)] if loc else []) + [f"• {l}" for l in lines_html]
        + [f"<b>Проба:</b> {esc(probe_line)}", f"<b>Время:</b> {esc(time_str)}"]
    )
    link_plain = f"\n\n🔗 {page_url}" if page_url else ""
    link_html = f'\n\n🔗 <a href="{esc(page_url)}">Открыть статус-страницу</a>' if page_url else ""

    text_plain = f"{head_plain}\n\n{body_plain}{link_plain}"
    text_html = f"{head_html}\n\n{body_html}{link_html}"

    outcomes: list[tuple[int, bool, str | None]] = []
    for ch in channels:
        cfg = ch.config or {}
        subscribed = cfg.get("events")
        if subscribed and "opened" not in subscribed:
            continue
        ok, err = True, None
        try:
            if ch.type == "telegram":
                _deliver(lambda cfg=cfg: _send_telegram(cfg, text_html, "HTML"))
            elif ch.type == "webhook":
                payload = {"event": "opened", "group": True, "count": len(records),
                           "servers": records, "occurred_at": occurred_at.isoformat(), "text": text_plain}
                _deliver(lambda cfg=cfg, payload=payload: _send_webhook(cfg, payload))
            elif ch.type == "email":
                _deliver(lambda cfg=cfg: _send_email(
                    cfg, subject=f"🔴 Сбой: {len(records)} серверов", text=text_plain))
        except Exception as exc:  # noqa: BLE001
            ok, err = False, str(exc)
            logger.warning("group alert channel %s (%s) failed: %s", ch.id, ch.type, exc)
        outcomes.append((ch.id, ok, err))
    record_deliveries(outcomes)


def dispatch(channels: list[AlertChannel], *, event: str, probe_name: str, server_host: str,
             status: str, error: str | None, server_id: int | None = None, group: bool = True,
             probe_type: str = "", server_name: str = "", service_name: str = "",
             page_title: str = "", page_url: str = "", latency_ms: float | None = None,
             started_at: datetime | None = None, occurred_at: datetime | None = None,
             alert_count: int | None = None) -> None:
    """Send a richly-formatted alert to the given channels (best-effort, storm-grouped)."""
    if not channels:
        return
    if group and not _group_allows(server_id, event):
        logger.info("alert grouped/suppressed: server=%s event=%s", server_id, event)
        return

    now = occurred_at or datetime.now(timezone.utc)
    duration = _fmt_duration((now - started_at).total_seconds()) if started_at else ""
    verb_emoji, verb_title = _header(event, status)
    verb = f"{verb_emoji} {verb_title}"

    ctx = {
        "event": event, "probe": probe_name, "type": probe_type, "host": server_host,
        "server": server_name, "service": service_name, "page": page_title,
        "status": status, "error": error or "", "latency": latency_ms,
        "duration": duration, "url": page_url, "alert_count": alert_count,
        "time": now.strftime("%d.%m.%Y %H:%M:%S UTC"),
    }
    default_plain, default_html, head_plain, rows = _build_messages(ctx)
    table_blocks = _table_blocks(head_plain, rows, page_url)

    # Template fields (strings) for custom templates.
    fields = {**ctx, "verb": verb, "latency": f"{latency_ms:.0f}" if latency_ms is not None else "",
              "alert_count": str(alert_count) if alert_count is not None else ""}

    outcomes: list[tuple[int, bool, str | None]] = []
    for ch in channels:
        cfg = ch.config or {}
        # Per-channel event subscription: empty/absent list = receive every event.
        subscribed = cfg.get("events")
        if subscribed and event not in subscribed:
            continue
        has_tpl = bool(cfg.get("template"))
        text = _render_template(cfg.get("template"), fields, default_plain)
        # Custom template -> send plain; default -> use HTML formatting (Telegram).
        tg_text = text if has_tpl else default_html
        tg_mode = None if has_tpl else "HTML"
        payload = {"event": event, "probe": probe_name, "type": probe_type,
                   "host": server_host, "server": server_name, "service": service_name,
                   "page": page_title, "status": status, "error": error,
                   "latency_ms": latency_ms, "duration": duration, "url": page_url,
                   "occurred_at": now.isoformat(), "text": text}
        ok, err = True, None
        try:
            if ch.type == "telegram":
                if not has_tpl and cfg.get("message_style") == "table":
                    try:
                        _deliver(lambda cfg=cfg, blocks=table_blocks: _send_telegram_rich(cfg, blocks))
                    except Exception as exc:  # noqa: BLE001 — Rich Message rejected, fall back to plain HTML
                        logger.warning("telegram Rich Message table failed (channel %s), "
                                       "falling back to classic HTML: %s", ch.id, exc)
                        _deliver(lambda cfg=cfg, tg_text=tg_text, tg_mode=tg_mode: _send_telegram(cfg, tg_text, tg_mode))
                else:
                    _deliver(lambda cfg=cfg, tg_text=tg_text, tg_mode=tg_mode: _send_telegram(cfg, tg_text, tg_mode))
            elif ch.type == "webhook":
                _deliver(lambda cfg=cfg, payload=payload: _send_webhook(cfg, payload))
            elif ch.type == "email":
                _deliver(lambda cfg=cfg, text=text: _send_email(cfg, subject=f"{verb}: {probe_name}", text=text))
        except Exception as exc:  # noqa: BLE001
            ok, err = False, str(exc)
            logger.warning("alert channel %s (%s) failed after %s attempt(s): %s",
                           ch.id, ch.type, max(1, settings.alert_retry_attempts), exc)
        outcomes.append((ch.id, ok, err))
    record_deliveries(outcomes)
