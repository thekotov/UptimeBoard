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
from app.models.alert import AlertChannel, AppSettings
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


fmt_duration = _fmt_duration  # public alias for use outside this module

# Long error text (raw HTTP bodies, stack traces) gets hidden behind a tap
# instead of dumping noise straight into the alert — <tg-spoiler> has worked in
# classic sendMessage HTML since long before Rich Messages existed.
_SPOILER_THRESHOLD = 80


def _error_html(text: str, esc, enhanced: bool = False) -> str:
    """<code> for a single-line error, or (only in the fallback-safe "enhanced"
    attempt) <pre> for a multi-line one so stack traces/JSON keep their line
    breaks instead of being flattened. <pre> nested inside <tg-spoiler> is a
    new Bot API 10.1/10.2 combination we haven't confirmed live — gated behind
    enhanced so a rejection falls back to the always-safe single-line <code>."""
    if enhanced and "\n" in text:
        body = f"<pre>{esc(text)}</pre>"
    else:
        body = f"<code>{esc(text)}</code>"
    return f"<tg-spoiler>{body}</tg-spoiler>" if len(text) > _SPOILER_THRESHOLD else body


# Emoji prefix per field row in the "default" style's code block — purely
# cosmetic, matches the Zabbix-style reference the layout was modelled on.
_ROW_ICONS = {
    "Проба": "🔍", "Где": "🌐", "Время ответа": "📶", "Ошибка": "💬",
    "IP": "🌐", "Сертификат": "📜", "Контент": "🔁",
    "Простой": "✅", "Длится уже": "⏱", "Алертов по инциденту": "🔔", "Время": "📅",
}


def _build_messages(ctx: dict, enhanced: bool = False) -> tuple[str, str, str, list[tuple[str, str, str]]]:
    """Build (plain, html, head_plain, rows) for an alert.

    Layout: "emoji + server · host" on its own line, the event title in bold
    below it, an optional "📌 reason" line when there's a short single-line
    error to call out, and the field list as a monospace "code block"
    (<pre><code class="language-java">> — Telegram's client applies its own
    syntax highlighting to plain key: value text, the same trick status-bot
    integrations like Zabbix use). <pre>/<code> don't allow nested formatting
    tags, so the code block always uses plain-escaped values, not the html
    tuple element (kept in ``rows`` for the "table" style, which also only
    reads the plain value). head_plain/rows are exposed separately so the
    "table" style can reuse the same fields inside a Rich Message table block.

    enhanced=True additionally marks the status word with <mark> and wraps the
    event line in <aside> (pull-quote) for "down" alerts — new Bot API 10.1/
    10.2 HTML tags, unconfirmed in classic sendMessage. Callers must be ready
    to fall back to enhanced=False (identical to the pre-existing behavior)
    if the send 400s."""
    emoji, title = _header(ctx["event"], ctx["status"])
    status_ru = _STATUS_RU.get(ctx["status"], ctx["status"])

    def esc(v) -> str:
        return html.escape(str(v)) if v is not None else ""

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
        rows.append((err_label, ctx["error"], esc(ctx["error"])))
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
    suffix_html = f" · <mark>{esc(status_ru)}</mark>" if (enhanced and suffix) else esc(suffix)

    head_plain = f"{emoji} {server_plain}\n{title.upper()}{suffix.upper()}"
    event_html = f"<b>{esc(title)}</b>{suffix_html}"
    if enhanced and ctx["status"] == "down":
        event_html = f"<aside>{event_html}</aside>"
    head_html = f"{emoji} {server_html}\n{event_html}"

    # A short single-line reason gets pinned right under the headline; long or
    # multi-line ones just stay in the code block below instead of wrapping a
    # slab of raw text in bold.
    pin_plain = pin_html = ""
    err_text = ctx.get("error") or ""
    if err_text and len(err_text) <= _SPOILER_THRESHOLD and "\n" not in err_text:
        pin_plain = f"\n\n📌 {err_text}"
        pin_html = f"\n\n📌 <b>{esc(err_text)}</b>"

    body_plain = "\n".join(f"{label}: {pv}" for label, pv, _ in rows)
    # <pre>/<code> render their content verbatim (no nested tag interpretation),
    # but it's still parsed as part of the surrounding HTML — the plain value
    # must be escaped or a "<"/"&" in it (URLs, raw error bodies) would corrupt
    # the markup.
    code_lines = "\n".join(f"{_ROW_ICONS.get(label, '•')} {label}: {esc(pv)}" for label, pv, _ in rows)

    link_plain = link_html = ""
    if ctx.get("url"):
        link_plain = f"\n\n🔗 {ctx['url']}"
        link_html = f'\n\n🔗 <a href="{esc(ctx["url"])}">Открыть статус-страницу</a>'

    return (f"{head_plain}{pin_plain}\n\n{body_plain}{link_plain}",
            f'{head_html}{pin_html}\n\n<pre><code class="language-java">{code_lines}</code></pre>{link_html}',
            head_plain, rows)


def _compact_message(ctx: dict, enhanced: bool = False) -> tuple[str, str]:
    """One-line format for busy/high-volume chats: status, server, the single
    most relevant detail (error, or downtime on resolve), type and time — no
    body list, no blockquote. Matches the "Компакт для шумных чатов" mockup.
    enhanced=True marks a non-error detail with <mark> (see _build_messages)."""
    def esc(v) -> str:
        return html.escape(str(v)) if v is not None else ""

    emoji, _ = _header(ctx["event"], ctx["status"])
    srv = ctx.get("server") or ctx["host"]

    if ctx["event"] == "resolved" and ctx.get("duration"):
        detail = f"восстановлен, простой {ctx['duration']}"
    elif ctx.get("error"):
        detail = ctx["error"]
    else:
        detail = _STATUS_RU.get(ctx["status"], ctx["status"])

    meta = ", ".join(x for x in ((ctx.get("type") or "").upper(), ctx["time"]) if x)
    link_plain = f" · 🔗 {ctx['url']}" if ctx.get("url") else ""
    link_html = f' · 🔗 <a href="{esc(ctx["url"])}">статус</a>' if ctx.get("url") else ""

    if ctx.get("error"):
        detail_html = _error_html(detail, esc, enhanced)
    elif enhanced:
        detail_html = f"<mark>{esc(detail)}</mark>"
    else:
        detail_html = f"<code>{esc(detail)}</code>"
    plain = f"{emoji} {srv} → {detail} ({meta}){link_plain}"
    html_ = f"{emoji} <b>{esc(srv)}</b> → {detail_html} ({esc(meta)}){link_html}"
    return plain, html_


# Telegram-only: how a channel renders its message. "default" is the classic
# HTML text above; "compact" is a one-liner for busy chats (_compact_message);
# "table" sends the fields as a Rich Message table via the Bot API 10.1+
# sendRichMessage method (see _table_blocks/_send_telegram_rich).
TELEGRAM_MESSAGE_STYLES = ("default", "compact", "table")


def _table_blocks(head_plain: str, rows: list[tuple[str, str, str]], url: str | None,
                   occurred_at: datetime | None = None) -> list[dict]:
    """Rich Message blocks for a Telegram sendRichMessage table alert.

    Field names match RichBlockTable/RichBlockTableCell per the Bot API 10.1/
    10.2 @grammyjs/types reference: is_bordered (not "bordered"), is_header
    (not "header"), and align/valign are mandatory on every cell — earlier
    attempts omitted them, which is the likely reason the table was accepted
    (no 400) but rendered empty. The "Время" cell uses a date_time RichText
    node (renders in the viewer's own timezone/format) instead of our
    pre-formatted UTC string, when occurred_at is available."""
    def _cell(text: str, header: bool = False, valign: str = "top") -> dict:
        cell: dict = {"text": text, "align": "left", "valign": valign}
        if header:
            cell["is_header"] = True
        return cell

    blocks: list[dict] = [{"type": "paragraph", "text": head_plain}]
    if rows:
        table_cells: list[list[dict]] = [
            [_cell("Поле", header=True, valign="middle"), _cell("Значение", header=True, valign="middle")],
        ]
        for label, value, _ in rows:
            if label == "Время" and occurred_at is not None:
                value_cell = {
                    "text": {"type": "date_time", "unix_time": int(occurred_at.timestamp()),
                              "date_time_format": "wDT"},
                    "align": "left", "valign": "top",
                }
            else:
                value_cell = _cell(value)
            table_cells.append([_cell(label), value_cell])
        blocks.append({
            "type": "table",
            "is_bordered": True,
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


def _deliver(fn):
    """Call a best-effort send function, retrying transient failures with
    exponential backoff. Returns fn()'s return value on success. Re-raises the
    last error if every attempt fails so the caller can log it."""
    attempts = max(1, settings.alert_retry_attempts)
    backoff = settings.alert_retry_backoff_sec
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if i + 1 < attempts:
                time.sleep(backoff * (2 ** i))
    if last is not None:
        raise last


def _send_telegram(config: dict, text: str, parse_mode: str | None = None,
                    reply_to: int | None = None, reply_markup: dict | None = None) -> int | None:
    token, chat_id = config.get("bot_token"), config.get("chat_id")
    if not token or not chat_id:
        logger.warning("telegram channel misconfigured")
        return None
    proxy = config.get("proxy") or None
    body: dict = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if parse_mode:
        body["parse_mode"] = parse_mode
    if reply_to:
        # allow_sending_without_reply: if the original message was since deleted,
        # send standalone instead of failing the whole alert.
        body["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
    if reply_markup is not None:
        body["reply_markup"] = reply_markup
    with httpx.Client(timeout=_TIMEOUT, proxy=proxy) as client:
        resp = client.post(f"https://api.telegram.org/bot{token}/sendMessage", json=body)
        resp.raise_for_status()
        return resp.json().get("result", {}).get("message_id")


def send_telegram_message(config: dict, text: str, parse_mode: str | None = None,
                           reply_markup: dict | None = None) -> int | None:
    """Public wrapper for sending a bot-command reply (e.g. /menu) — same as
    _send_telegram but named for use outside this module (telegram_menu.py)."""
    return _send_telegram(config, text, parse_mode=parse_mode, reply_markup=reply_markup)


def edit_telegram_message(config: dict, message_id: int, text: str,
                           parse_mode: str | None = "HTML", reply_markup: dict | None = None) -> None:
    """Edit a previously-sent message in place (used to update a /menu screen
    without spamming a new message per button tap)."""
    token, chat_id = config.get("bot_token"), config.get("chat_id")
    if not token or not chat_id:
        return
    proxy = config.get("proxy") or None
    body: dict = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if parse_mode:
        body["parse_mode"] = parse_mode
    if reply_markup is not None:
        body["reply_markup"] = reply_markup
    with httpx.Client(timeout=_TIMEOUT, proxy=proxy) as client:
        resp = client.post(f"https://api.telegram.org/bot{token}/editMessageText", json=body)
        resp.raise_for_status()


def answer_telegram_callback(config: dict, callback_query_id: str, text: str | None = None) -> None:
    """Acknowledge a button tap (stops the client's loading spinner). Telegram
    expects this within a few seconds; best-effort, never raises."""
    token = config.get("bot_token")
    if not token:
        return
    proxy = config.get("proxy") or None
    body: dict = {"callback_query_id": callback_query_id}
    if text:
        body["text"] = text
    try:
        with httpx.Client(timeout=_TIMEOUT, proxy=proxy) as client:
            client.post(f"https://api.telegram.org/bot{token}/answerCallbackQuery", json=body)
    except Exception as exc:  # noqa: BLE001
        logger.warning("answerCallbackQuery failed: %s", exc)


def set_telegram_webhook(config: dict, url: str) -> None:
    """Register the inbound webhook for /menu commands. Raises on failure so
    the admin action surfaces the real error. Note: Telegram disables
    getUpdates (used by "pick chat_id") for a bot once a webhook is set."""
    token = config.get("bot_token")
    if not token:
        raise ValueError("bot_token required")
    proxy = config.get("proxy") or None
    body = {"url": url, "allowed_updates": ["message", "callback_query"]}
    with httpx.Client(timeout=_TIMEOUT, proxy=proxy) as client:
        resp = client.post(f"https://api.telegram.org/bot{token}/setWebhook", json=body)
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("description") or f"HTTP {resp.status_code}")


def delete_telegram_webhook(config: dict) -> None:
    """Best-effort: restore getUpdates capability for the bot when /menu is
    disabled. Never raises."""
    token = config.get("bot_token")
    if not token:
        return
    proxy = config.get("proxy") or None
    try:
        with httpx.Client(timeout=_TIMEOUT, proxy=proxy) as client:
            client.post(f"https://api.telegram.org/bot{token}/deleteWebhook", json={})
    except Exception as exc:  # noqa: BLE001
        logger.warning("deleteWebhook failed: %s", exc)


def _send_telegram_rich(config: dict, blocks: list[dict], reply_to: int | None = None) -> int | None:
    """Send a Telegram Rich Message (Bot API 10.1+ sendRichMessage). Schema is
    best-effort (see _table_blocks) — raises on any non-2xx so the caller can
    fall back to the classic HTML message."""
    token, chat_id = config.get("bot_token"), config.get("chat_id")
    if not token or not chat_id:
        logger.warning("telegram channel misconfigured")
        return None
    proxy = config.get("proxy") or None
    body: dict = {"chat_id": chat_id, "rich_message": {"blocks": blocks}}
    if reply_to:
        body["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
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
        return resp.json().get("result", {}).get("message_id")


# --- Reply-chain threading: alerts for the same server's incident (opened ->
# ongoing/escalated -> resolved) reply to the first message of that incident
# instead of arriving as unrelated messages, so the whole lifecycle stays
# visually grouped in the chat. Keyed by (channel, server) in Redis — same
# storage style as the storm-grouping gate above, best-effort (a Redis miss
# just means the next alert starts a fresh thread instead of chaining).
_THREAD_EVENTS = ("opened", "ongoing", "escalated", "resolved")
_THREAD_TTL_SEC = 24 * 3600


def _thread_key(channel_id: int, server_id: int) -> str:
    return f"alert:tg:thread:{channel_id}:{server_id}"


def _thread_get(channel_id: int, server_id: int) -> int | None:
    try:
        r = get_sync_redis()
        key = _thread_key(channel_id, server_id)
        val = r.get(key)
        if val is None:
            return None
        r.expire(key, _THREAD_TTL_SEC)  # keep the thread alive while a long incident drags on
        return int(val)
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram reply-thread lookup failed: %s", exc)
        return None


def _thread_set(channel_id: int, server_id: int, message_id: int) -> None:
    try:
        get_sync_redis().set(_thread_key(channel_id, server_id), message_id, ex=_THREAD_TTL_SEC)
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram reply-thread store failed: %s", exc)


def _thread_clear(channel_id: int, server_id: int) -> None:
    try:
        get_sync_redis().delete(_thread_key(channel_id, server_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram reply-thread clear failed: %s", exc)


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
    default_plain, _, head_plain, rows = _build_messages(ctx)
    fields = {**ctx, "verb": verb,
              "latency": f"{ctx['latency']:.0f}" if ctx.get("latency") is not None else ""}
    template = config.get("template")
    if not template:
        # Telegram sends the rich HTML body (or a Rich Message table); webhook/email
        # get the plain mirror.
        if channel_type == "telegram":
            style = config.get("message_style")
            if style == "table":
                return {"text": _table_preview_html(head_plain, rows, ctx.get("url")), "is_html": True}
            if style == "compact":
                _, compact_html = _compact_message(ctx, enhanced=True)
                return {"text": compact_html, "is_html": True}
            _, default_html_enhanced, _, _ = _build_messages(ctx, enhanced=True)
            return {"text": default_html_enhanced, "is_html": True}
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
    sample_ctx = _sample_ctx()
    _, default_html, head_plain, rows = _build_messages(sample_ctx)
    _, default_html_enhanced, _, _ = _build_messages(sample_ctx, enhanced=True)
    style = config.get("message_style")
    if style == "table":
        try:
            _send_telegram_rich(config, _table_blocks("🧪 ТЕСТ\n" + head_plain, rows, None,
                                                       occurred_at=datetime.now(timezone.utc)))
            return True, "Тестовая Rich Message-таблица отправлена в Telegram"
        except Exception as exc:  # noqa: BLE001
            return False, f"sendRichMessage не сработал ({exc}); в реальных алертах сработает откат на обычный HTML"
    if style == "compact":
        _, compact_html = _compact_message(sample_ctx)
        _, compact_html_enhanced = _compact_message(sample_ctx, enhanced=True)
        try:
            _send_telegram(config, "🧪 ТЕСТ " + compact_html_enhanced, parse_mode="HTML")
            return True, "Тестовый алерт отправлен в Telegram (расширенное форматирование)"
        except Exception as exc:  # noqa: BLE001
            logger.warning("test compact enhanced HTML failed, falling back to plain: %s", exc)
            try:
                _send_telegram(config, "🧪 ТЕСТ " + compact_html, parse_mode="HTML")
                return True, "Тестовый алерт отправлен (обычное форматирование — <mark>/<pre> Telegram не принял)"
            except Exception as exc2:  # noqa: BLE001
                return False, str(exc2)
    text_enhanced = "🧪 <b>ТЕСТ</b>\n\n" + default_html_enhanced
    try:
        _send_telegram(config, text_enhanced, parse_mode="HTML")
        return True, "Тестовый алерт отправлен в Telegram (расширенное форматирование)"
    except Exception as exc:  # noqa: BLE001
        logger.warning("test default enhanced HTML failed, falling back to plain: %s", exc)
    text = "🧪 <b>ТЕСТ</b>\n\n" + default_html
    try:
        _send_telegram(config, text, parse_mode="HTML")
        return True, "Тестовый алерт отправлен (обычное форматирование — <mark>/<aside>/<pre> Telegram не принял)"
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
    plain, _, _, _ = _build_messages(ctx)
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
    plain, _, _, _ = _build_messages(_sample_ctx())
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


def get_alert_storm_window_sec(db: Session) -> int:
    """Effective storm-grouping window: the admin-set override in AppSettings
    (id=1), falling back to settings.alert_storm_window_sec (env default) when
    no override row exists or its column is NULL."""
    row = db.get(AppSettings, 1)
    if row is not None and row.alert_storm_window_sec is not None:
        return row.alert_storm_window_sec
    return settings.alert_storm_window_sec


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


# dispatch_group sends via classic sendMessage (not sendRichMessage), whose
# text limit is 4096 UTF-16 code units — a different, much smaller limit than
# Rich Messages' 32768. Cap the listed servers so a storm across many servers
# can never blow past it, regardless of how long server/error names are.
_GROUP_LIST_CAP = 25


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

    shown = records[:_GROUP_LIST_CAP]
    overflow = len(records) - len(shown)

    lines_plain = [
        f"{r.get('server_name', '')} {r.get('server_host', '')} — {r.get('error') or r.get('status', '')}"
        for r in shown
    ]
    lines_html = [
        f"{esc(r.get('server_name', ''))} · <code>{esc(r.get('server_host', ''))}</code> — "
        f"<code>{esc(r.get('error') or r.get('status', ''))}</code>"
        for r in shown
    ]
    if overflow > 0:
        lines_plain.append(f"…и ещё {overflow} серверов")
        lines_html.append(f"…и ещё {overflow} серверов")

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
    if len(text_html) > 4096:
        # The _GROUP_LIST_CAP slice above should make this unreachable for
        # realistic name lengths; not truncating here since cutting HTML
        # mid-tag would break parse_mode=HTML outright. Telegram will reject
        # the send and the per-channel error below will surface it.
        logger.warning("group alert text exceeds Telegram's 4096-char limit (%d chars, %d servers)",
                        len(text_html), len(records))

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
    _, default_html_enhanced, _, _ = _build_messages(ctx, enhanced=True)
    table_blocks = _table_blocks(head_plain, rows, page_url, occurred_at=now)
    _, compact_html = _compact_message(ctx)
    _, compact_html_enhanced = _compact_message(ctx, enhanced=True)

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
        threadable = server_id is not None and event in _THREAD_EVENTS
        reply_to = _thread_get(ch.id, server_id) if (threadable and event != "opened") else None

        ok, err = True, None
        try:
            if ch.type == "telegram":
                style = cfg.get("message_style") if not has_tpl else None
                if style == "table":
                    try:
                        msg_id = _deliver(lambda cfg=cfg, blocks=table_blocks, reply_to=reply_to:
                                           _send_telegram_rich(cfg, blocks, reply_to))
                    except Exception as exc:  # noqa: BLE001 — Rich Message rejected, fall back to plain HTML
                        logger.warning("telegram Rich Message table failed (channel %s), "
                                       "falling back to classic HTML: %s", ch.id, exc)
                        msg_id = _deliver(lambda cfg=cfg, tg_text=tg_text, tg_mode=tg_mode, reply_to=reply_to:
                                           _send_telegram(cfg, tg_text, tg_mode, reply_to))
                elif style == "compact":
                    try:
                        msg_id = _deliver(lambda cfg=cfg, tg_text=compact_html_enhanced, reply_to=reply_to:
                                           _send_telegram(cfg, tg_text, "HTML", reply_to))
                    except Exception as exc:  # noqa: BLE001 — <mark>/<pre> combo rejected, fall back to plain
                        logger.warning("telegram enhanced compact HTML failed (channel %s), "
                                       "falling back to plain: %s", ch.id, exc)
                        msg_id = _deliver(lambda cfg=cfg, tg_text=compact_html, reply_to=reply_to:
                                           _send_telegram(cfg, tg_text, "HTML", reply_to))
                elif not has_tpl:
                    try:
                        msg_id = _deliver(lambda cfg=cfg, tg_text=default_html_enhanced, reply_to=reply_to:
                                           _send_telegram(cfg, tg_text, "HTML", reply_to))
                    except Exception as exc:  # noqa: BLE001 — <mark>/<aside>/<pre> combo rejected, fall back
                        logger.warning("telegram enhanced default HTML failed (channel %s), "
                                       "falling back to plain: %s", ch.id, exc)
                        msg_id = _deliver(lambda cfg=cfg, tg_text=tg_text, tg_mode=tg_mode, reply_to=reply_to:
                                           _send_telegram(cfg, tg_text, tg_mode, reply_to))
                else:
                    msg_id = _deliver(lambda cfg=cfg, tg_text=tg_text, tg_mode=tg_mode, reply_to=reply_to:
                                       _send_telegram(cfg, tg_text, tg_mode, reply_to))
                if threadable and msg_id:
                    if event == "resolved":
                        _thread_clear(ch.id, server_id)
                    elif reply_to is None:
                        _thread_set(ch.id, server_id, msg_id)
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
