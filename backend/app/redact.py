"""Redaction of Telegram bot tokens in logs and error strings.

httpx puts the full request URL into its exceptions and INFO logs, and Bot API
URLs carry the token (`/bot<id>:<secret>/sendMessage`). Without this, any failed
delivery would write the token to container logs and to the channel's stored
`last_error` shown in the admin UI.
"""

import logging
import re

_TOKEN_RE = re.compile(r"bot\d{5,}:[A-Za-z0-9_-]{20,}")


def scrub(value: object) -> str:
    """Return str(value) with any bot token replaced by `bot<token>`."""
    return _TOKEN_RE.sub("bot<token>", str(value))


_installed = False


def install_log_redaction() -> None:
    """Scrub tokens from every log record (ours and third-party, e.g. httpx).
    Idempotent; safe to call from several modules."""
    global _installed
    if _installed:
        return
    _installed = True
    base_factory = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = base_factory(*args, **kwargs)
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 — never break logging over formatting
            return record
        clean = scrub(msg)
        if clean != msg:
            record.msg, record.args = clean, None
        if record.exc_info and not record.exc_text:
            # Formatter reuses exc_text when set — pre-render the traceback scrubbed.
            record.exc_text = scrub(logging.Formatter().formatException(record.exc_info))
        return record

    logging.setLogRecordFactory(factory)
