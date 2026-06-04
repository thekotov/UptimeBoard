"""Alert dispatch.

- Channels are split into "base" (escalate_after_min == 0, alert immediately) and
  "escalation" (escalate_after_min > 0, only after an incident has stayed open).
- Per-page routing: a channel with a page_id only receives that page's alerts.
- Storm grouping: "down"/"resolved" alerts for the same server are coalesced
  within a short window so a whole-server outage sends one alert, not one per probe.
"""

import html
import logging
import smtplib
import threading
import time
from datetime import datetime, timezone
from email.message import EmailMessage

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.alert import AlertChannel
from app.realtime import get_sync_redis

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
TEMPLATE_FIELDS = (
    "event", "verb", "probe", "type", "host", "server", "service", "page",
    "status", "error", "latency", "duration", "url", "time",
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


def _build_messages(ctx: dict) -> tuple[str, str]:
    """Build (plain, html) alert bodies. HTML uses Telegram-supported tags
    (<b>, <code>, <a>); plain mirrors it for email/webhook and template fallback."""
    emoji, title = _header(ctx["event"], ctx["status"])
    status_ru = _STATUS_RU.get(ctx["status"], ctx["status"])

    def esc(v) -> str:
        return html.escape(str(v)) if v is not None else ""

    rows: list[tuple[str, str, str]] = []
    rows.append(("Проба", ctx["probe"] + (f" · {ctx['type'].upper()}" if ctx.get("type") else ""),
                 esc(ctx["probe"]) + (f" · {esc(ctx['type'].upper())}" if ctx.get("type") else "")))
    srv = ctx.get("server") or ""
    rows.append(("Сервер", f"{srv} · {ctx['host']}".strip(" ·"),
                 (esc(srv) + " · " if srv else "") + f"<code>{esc(ctx['host'])}</code>"))
    loc = " · ".join(x for x in (ctx.get("service"), ctx.get("page")) if x)
    if loc:
        rows.append(("Где", loc, esc(loc)))
    if ctx.get("latency") is not None:
        lat = f"{ctx['latency']:.0f} мс"
        rows.append(("Время ответа", lat, esc(lat)))
    if ctx.get("error"):
        rows.append(("Ошибка", ctx["error"], f"<code>{esc(ctx['error'])}</code>"))
    if ctx.get("duration"):
        label = "Простой" if ctx["event"] == "resolved" else "Длится уже"
        rows.append((label, ctx["duration"], esc(ctx["duration"])))
    rows.append(("Время", ctx["time"], esc(ctx["time"])))

    # Append the current status only when it isn't already implied by the title
    # (avoids "Деградация · деградация" / "Всё ещё недоступен · недоступен").
    suffix = "" if status_ru.lower() in title.lower() else f" · {status_ru}"
    head_plain = f"{emoji} {title.upper()}{suffix.upper()}"
    head_html = f"{emoji} <b>{esc(title)}</b>{esc(suffix)}"
    body_plain = "\n".join(f"{label}: {pv}" for label, pv, _ in rows)
    body_html = "\n".join(f"<b>{esc(label)}:</b> {hv}" for label, _, hv in rows)

    link_plain = link_html = ""
    if ctx.get("url"):
        link_plain = f"\n\n🔗 {ctx['url']}"
        link_html = f'\n\n🔗 <a href="{esc(ctx["url"])}">Открыть статус-страницу</a>'

    return (f"{head_plain}\n\n{body_plain}{link_plain}",
            f"{head_html}\n\n{body_html}{link_html}")


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


def send_test_telegram(config: dict) -> tuple[bool, str]:
    """Deliver a sample formatted alert to the channel's Telegram chat so the
    operator can confirm the bot token, chat_id (and proxy) actually work
    end-to-end. Returns (ok, detail)."""
    if not config.get("bot_token"):
        return False, "bot_token required"
    if not config.get("chat_id"):
        return False, "chat_id required"
    _, default_html = _build_messages(_sample_ctx())
    text = "🧪 <b>ТЕСТ</b>\n\n" + default_html
    try:
        _send_telegram(config, text, parse_mode="HTML")
        return True, "Тестовый алерт отправлен в Telegram"
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


def dispatch(channels: list[AlertChannel], *, event: str, probe_name: str, server_host: str,
             status: str, error: str | None, server_id: int | None = None, group: bool = True,
             probe_type: str = "", server_name: str = "", service_name: str = "",
             page_title: str = "", page_url: str = "", latency_ms: float | None = None,
             started_at: datetime | None = None, occurred_at: datetime | None = None) -> None:
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
        "duration": duration, "url": page_url,
        "time": now.strftime("%d.%m.%Y %H:%M:%S UTC"),
    }
    default_plain, default_html = _build_messages(ctx)

    # Template fields (strings) for custom templates.
    fields = {**ctx, "verb": verb, "latency": f"{latency_ms:.0f}" if latency_ms is not None else ""}

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
        try:
            if ch.type == "telegram":
                _deliver(lambda cfg=cfg, tg_text=tg_text, tg_mode=tg_mode: _send_telegram(cfg, tg_text, tg_mode))
            elif ch.type == "webhook":
                _deliver(lambda cfg=cfg, payload=payload: _send_webhook(cfg, payload))
            elif ch.type == "email":
                _deliver(lambda cfg=cfg, text=text: _send_email(cfg, subject=f"{verb}: {probe_name}", text=text))
        except Exception as exc:  # noqa: BLE001
            logger.warning("alert channel %s (%s) failed after %s attempt(s): %s",
                           ch.id, ch.type, max(1, settings.alert_retry_attempts), exc)
