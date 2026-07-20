from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app import probe_runner as pr
from app.models.monitoring import STATUS_DEGRADED, STATUS_DOWN, STATUS_UNKNOWN, STATUS_UP
from app.probe_runner import _apply_latency_threshold, _display_status, _is_bad
from app.probes import ProbeOutcome


def _probe(latency_degraded_ms=None):
    return SimpleNamespace(latency_degraded_ms=latency_degraded_ms)


def _tier_probe(degraded_threshold=1, down_threshold=1, tolerance_checks=0):
    return SimpleNamespace(
        degraded_threshold=degraded_threshold,
        down_threshold=down_threshold,
        tolerance_checks=tolerance_checks,
    )


def test_is_bad():
    assert _is_bad(STATUS_DOWN) is True
    assert _is_bad(STATUS_DEGRADED) is True
    assert _is_bad(STATUS_UP) is False
    assert _is_bad(STATUS_UNKNOWN) is False


def test_latency_threshold_downgrades_when_slow():
    out = _apply_latency_threshold(_probe(100), ProbeOutcome(status=STATUS_UP, latency_ms=250))
    assert out.status == STATUS_DEGRADED
    assert "250" in out.error and "100" in out.error


def test_latency_threshold_keeps_fast_up():
    out = _apply_latency_threshold(_probe(100), ProbeOutcome(status=STATUS_UP, latency_ms=50))
    assert out.status == STATUS_UP


def test_latency_threshold_ignored_without_limit():
    out = _apply_latency_threshold(_probe(None), ProbeOutcome(status=STATUS_UP, latency_ms=9999))
    assert out.status == STATUS_UP


def test_latency_threshold_does_not_touch_down():
    out = _apply_latency_threshold(_probe(100), ProbeOutcome(status=STATUS_DOWN, latency_ms=None))
    assert out.status == STATUS_DOWN


def test_display_status_default_thresholds_down_immediately():
    # Defaults (1, 1): a hard-down check is "down" from the first failure.
    p = _tier_probe()
    assert _display_status(p, STATUS_DOWN, 1) == STATUS_DOWN
    assert _display_status(p, STATUS_DOWN, 5) == STATUS_DOWN


def test_display_status_three_tiers():
    # degraded after 1 failure, down after 3.
    p = _tier_probe(degraded_threshold=1, down_threshold=3)
    assert _display_status(p, STATUS_DOWN, 1) == STATUS_DEGRADED
    assert _display_status(p, STATUS_DOWN, 2) == STATUS_DEGRADED
    assert _display_status(p, STATUS_DOWN, 3) == STATUS_DOWN
    assert _display_status(p, STATUS_DOWN, 9) == STATUS_DOWN


def test_display_status_tolerates_early_failures():
    # degraded only after 2 failures: the first failure still shows up.
    p = _tier_probe(degraded_threshold=2, down_threshold=4)
    assert _display_status(p, STATUS_DOWN, 1) == STATUS_UP
    assert _display_status(p, STATUS_DOWN, 2) == STATUS_DEGRADED


def test_display_status_passes_through_non_down():
    # up / latency-degraded / unknown are never tiered.
    p = _tier_probe(degraded_threshold=1, down_threshold=3)
    assert _display_status(p, STATUS_UP, 0) == STATUS_UP
    assert _display_status(p, STATUS_DEGRADED, 5) == STATUS_DEGRADED
    assert _display_status(p, STATUS_UNKNOWN, 5) == STATUS_UNKNOWN


# ---- IP-change tracking (handle_ip_change) ----

_NOW = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)


class _FakeDB:
    """Minimal stand-in: the change handlers persist a ProbeEvent via db.add()."""

    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)


_DB = _FakeDB()


def _ip_probe(last_ip):
    server = SimpleNamespace(name="srv", service=SimpleNamespace(name="svc", page=None))
    return SimpleNamespace(id=1, name="p", type="http", server_id=2, server=server, last_ip=last_ip)


def _capture_dispatch(monkeypatch):
    calls = []
    monkeypatch.setattr(pr, "base_channels", lambda db, page_id: ["chan"])
    monkeypatch.setattr(pr, "dispatch", lambda channels, **kw: calls.append(kw))
    return calls


def test_ip_change_alerts_on_change(monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    probe = _ip_probe("1.1.1.1")
    outcome = ProbeOutcome(status=STATUS_UP, meta={"resolved_ips": ["2.2.2.2"]})
    pr.handle_ip_change(_DB, probe, outcome, "example.com", 3, False, _NOW)
    assert probe.last_ip == "2.2.2.2"
    assert len(calls) == 1
    assert calls[0]["event"] == "ip_changed"
    assert calls[0]["group"] is False
    assert "1.1.1.1" in calls[0]["error"] and "2.2.2.2" in calls[0]["error"]


def test_ip_change_first_observation_is_silent(monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    probe = _ip_probe(None)
    outcome = ProbeOutcome(status=STATUS_UP, meta={"resolved_ips": ["1.1.1.1"]})
    pr.handle_ip_change(_DB, probe, outcome, "example.com", 3, False, _NOW)
    assert probe.last_ip == "1.1.1.1"
    assert calls == []


def test_ip_change_same_set_no_alert(monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    probe = _ip_probe("1.1.1.1, 2.2.2.2")
    outcome = ProbeOutcome(status=STATUS_UP, meta={"resolved_ips": ["1.1.1.1", "2.2.2.2"]})
    pr.handle_ip_change(_DB, probe, outcome, "example.com", 3, False, _NOW)
    assert calls == []


def test_ip_change_suppressed_in_maintenance(monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    probe = _ip_probe("1.1.1.1")
    outcome = ProbeOutcome(status=STATUS_UP, meta={"resolved_ips": ["2.2.2.2"]})
    pr.handle_ip_change(_DB, probe, outcome, "example.com", 3, True, _NOW)
    # still records the new set, but sends nothing during maintenance
    assert probe.last_ip == "2.2.2.2"
    assert calls == []


def test_ip_change_no_meta_is_noop(monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    probe = _ip_probe("1.1.1.1")
    pr.handle_ip_change(_DB, probe, ProbeOutcome(status=STATUS_UP), "example.com", 3, False, _NOW)
    assert probe.last_ip == "1.1.1.1"
    assert calls == []


# ---- SSL-change tracking (handle_cert_change) ----


def _cert_probe(track, issuer="Let's Encrypt", subject="example.com", expires=None):
    server = SimpleNamespace(name="srv", service=SimpleNamespace(name="svc", page=None))
    # tls_* fields hold the just-applied (new) metadata; handle_cert_change gets the
    # previous snapshot separately.
    return SimpleNamespace(
        id=1, name="p", type="tls", server_id=2, server=server,
        config={"track_cert_change": track},
        tls_issuer=issuer, tls_subject=subject, tls_expires_at=expires,
    )


def test_cert_change_alerts_on_new_fingerprint(monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    probe = _cert_probe(True, issuer="ZeroSSL")
    outcome = ProbeOutcome(status=STATUS_UP, meta={"fingerprint": "new"})
    prev = ("old", "Let's Encrypt", "example.com")  # (fp, issuer, subject)
    pr.handle_cert_change(_DB, probe, outcome, "example.com", 3, False, _NOW, prev)
    assert len(calls) == 1
    assert calls[0]["event"] == "cert_changed" and calls[0]["group"] is False
    # issuer moved -> shown as old → new
    assert "Let's Encrypt" in calls[0]["error"] and "ZeroSSL" in calls[0]["error"]


def test_cert_change_first_observation_is_silent(monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    probe = _cert_probe(True)
    outcome = ProbeOutcome(status=STATUS_UP, meta={"fingerprint": "first"})
    pr.handle_cert_change(_DB, probe, outcome, "h", 3, False, _NOW, (None, None, None))
    assert calls == []


def test_cert_change_same_fingerprint_no_alert(monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    probe = _cert_probe(True)
    outcome = ProbeOutcome(status=STATUS_UP, meta={"fingerprint": "same"})
    pr.handle_cert_change(_DB, probe, outcome, "h", 3, False, _NOW, ("same", "i", "s"))
    assert calls == []


def test_cert_change_disabled_is_noop(monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    probe = _cert_probe(False)
    outcome = ProbeOutcome(status=STATUS_UP, meta={"fingerprint": "new"})
    pr.handle_cert_change(_DB, probe, outcome, "h", 3, False, _NOW, ("old", "i", "s"))
    assert calls == []


def test_cert_change_suppressed_in_maintenance(monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    probe = _cert_probe(True)
    outcome = ProbeOutcome(status=STATUS_UP, meta={"fingerprint": "new"})
    pr.handle_cert_change(_DB, probe, outcome, "h", 3, True, _NOW, ("old", "i", "s"))
    assert calls == []


def test_cert_change_no_fingerprint_is_noop(monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    probe = _cert_probe(True)
    pr.handle_cert_change(_DB, probe, ProbeOutcome(status=STATUS_UP), "h", 3, False, _NOW, ("old", "i", "s"))
    assert calls == []


# ---- content-change tracking (handle_content_change) ----


def _content_probe(track, last_hash):
    server = SimpleNamespace(name="srv", service=SimpleNamespace(name="svc", page=None))
    return SimpleNamespace(id=1, name="p", type="http", server_id=2, server=server,
                           config={"track_content": track}, last_content_hash=last_hash)


def test_content_change_alerts_on_change(monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    probe = _content_probe(True, "aaaaaaaaaaaaaa")
    outcome = ProbeOutcome(status=STATUS_UP, meta={"content_hash": "bbbbbbbbbbbbbb"})
    pr.handle_content_change(_DB, probe, outcome, "h", 3, False, _NOW)
    assert probe.last_content_hash == "bbbbbbbbbbbbbb"
    assert len(calls) == 1 and calls[0]["event"] == "content_changed" and calls[0]["group"] is False


def test_content_change_first_observation_is_silent(monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    probe = _content_probe(True, None)
    pr.handle_content_change(_DB, probe, ProbeOutcome(status=STATUS_UP, meta={"content_hash": "x"}), "h", 3, False, _NOW)
    assert probe.last_content_hash == "x" and calls == []


def test_content_change_same_hash_no_alert(monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    probe = _content_probe(True, "same")
    pr.handle_content_change(_DB, probe, ProbeOutcome(status=STATUS_UP, meta={"content_hash": "same"}), "h", 3, False, _NOW)
    assert calls == []


def test_content_change_suppressed_in_maintenance(monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    probe = _content_probe(True, "a")
    pr.handle_content_change(_DB, probe, ProbeOutcome(status=STATUS_UP, meta={"content_hash": "b"}), "h", 3, True, _NOW)
    assert probe.last_content_hash == "b" and calls == []


def test_content_change_no_meta_is_noop(monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    probe = _content_probe(True, "a")
    pr.handle_content_change(_DB, probe, ProbeOutcome(status=STATUS_UP), "h", 3, False, _NOW)
    assert probe.last_content_hash == "a" and calls == []


# ---- cert-expiry reminders (handle_cert_expiry) ----


def test_cert_reminder_bucket():
    th = [1, 7, 14]
    assert pr._cert_reminder_bucket(20, th) is None
    assert pr._cert_reminder_bucket(14, th) == 14
    assert pr._cert_reminder_bucket(10, th) == 14
    assert pr._cert_reminder_bucket(7, th) == 7
    assert pr._cert_reminder_bucket(3, th) == 7
    assert pr._cert_reminder_bucket(1, th) == 1
    assert pr._cert_reminder_bucket(-2, th) == 1


def _expiry_probe(days_left, reminders=True, reminder_days=None):
    server = SimpleNamespace(name="srv", service=SimpleNamespace(name="svc", page=None))
    cfg = {"cert_expiry_reminders": reminders}
    return SimpleNamespace(
        id=1, name="p", type="tls", server_id=2, server=server, config=cfg,
        tls_expires_at=_NOW + timedelta(days=days_left), tls_issuer="Let's Encrypt",
        tls_reminder_days=reminder_days,
    )


def test_cert_expiry_alerts_when_crossing_threshold(monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    probe = _expiry_probe(10)  # inside 14-day bucket, none sent yet
    pr.handle_cert_expiry(_DB, probe, ProbeOutcome(status=STATUS_UP), "h", 3, False, _NOW)
    assert probe.tls_reminder_days == 14
    assert len(calls) == 1 and calls[0]["event"] == "cert_expiring"
    assert "истекает через 10" in calls[0]["error"]


def test_cert_expiry_no_repeat_same_bucket(monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    probe = _expiry_probe(10, reminder_days=14)  # already reminded at 14
    pr.handle_cert_expiry(_DB, probe, ProbeOutcome(status=STATUS_UP), "h", 3, False, _NOW)
    assert calls == []


def test_cert_expiry_realerts_on_tighter_bucket(monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    probe = _expiry_probe(5, reminder_days=14)  # was 14, now inside 7
    pr.handle_cert_expiry(_DB, probe, ProbeOutcome(status=STATUS_UP), "h", 3, False, _NOW)
    assert probe.tls_reminder_days == 7 and len(calls) == 1


def test_cert_expiry_resets_after_renewal(monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    probe = _expiry_probe(80, reminder_days=7)  # renewed: plenty of days again
    pr.handle_cert_expiry(_DB, probe, ProbeOutcome(status=STATUS_UP), "h", 3, False, _NOW)
    assert probe.tls_reminder_days is None and calls == []


def test_cert_expiry_suppressed_in_maintenance(monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    probe = _expiry_probe(3)
    pr.handle_cert_expiry(_DB, probe, ProbeOutcome(status=STATUS_UP), "h", 3, True, _NOW)
    # neither alerts nor consumes the reminder, so it still fires post-maintenance
    assert probe.tls_reminder_days is None and calls == []


def test_cert_expiry_disabled_or_no_cert_is_noop(monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    pr.handle_cert_expiry(_DB, _expiry_probe(3, reminders=False), ProbeOutcome(status=STATUS_UP), "h", 3, False, _NOW)
    noexp = _expiry_probe(3)
    noexp.tls_expires_at = None
    pr.handle_cert_expiry(_DB, noexp, ProbeOutcome(status=STATUS_UP), "h", 3, False, _NOW)
    assert calls == []
